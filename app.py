"""Kaspi expense analyzer — Flask web app.

Usage:
    pip install -r requirements.txt
    python app.py
    # open http://127.0.0.1:5000 and upload a Kaspi PDF statement

Data is persisted in a local SQLite file (kaspi.db). The PDF is parsed once,
transactions are stored, and any manual edits (category override, note) live
in the same DB so they survive re-uploads of the same statement.
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
from collections import defaultdict
from pathlib import Path

from flask import Flask, g, jsonify, redirect, render_template, request, url_for

from categorizer import ALL_CATEGORIES, categorize
from parser import parse_pdf

APP_ROOT = Path(__file__).parent
DB_PATH = APP_ROOT / "kaspi.db"

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024  # 16 MB


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS txns (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    fingerprint     TEXT UNIQUE NOT NULL,
    date            TEXT NOT NULL,
    amount          REAL NOT NULL,
    operation       TEXT NOT NULL,
    details         TEXT NOT NULL,
    auto_category   TEXT NOT NULL,
    user_category   TEXT,
    note            TEXT,
    created_at      TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_txns_date ON txns(date);
"""


def get_db() -> sqlite3.Connection:
    db = getattr(g, "_db", None)
    if db is None:
        db = g._db = sqlite3.connect(DB_PATH)
        db.row_factory = sqlite3.Row
        db.executescript(SCHEMA)
    return db


@app.teardown_appcontext
def close_db(_exc):  # noqa: ANN001
    db = getattr(g, "_db", None)
    if db is not None:
        db.close()


def init_db() -> None:
    with sqlite3.connect(DB_PATH) as db:
        db.executescript(SCHEMA)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    db = get_db()
    category_filter = request.args.get("category", "").strip()
    direction = request.args.get("direction", "").strip()  # "in" / "out" / ""
    search = request.args.get("q", "").strip()

    sql = "SELECT * FROM txns WHERE 1=1"
    params: list = []
    if category_filter:
        sql += " AND COALESCE(user_category, auto_category) = ?"
        params.append(category_filter)
    if direction == "in":
        sql += " AND amount > 0"
    elif direction == "out":
        sql += " AND amount < 0"
    if search:
        sql += " AND (details LIKE ? OR operation LIKE ? OR COALESCE(note, '') LIKE ?)"
        like = f"%{search}%"
        params.extend([like, like, like])
    sql += " ORDER BY date DESC, id DESC"

    rows = db.execute(sql, params).fetchall()

    # Summary across the *filtered* set, grouped by effective category
    by_cat: dict[str, dict[str, float]] = defaultdict(lambda: {"in": 0.0, "out": 0.0, "n": 0})
    total_in = total_out = 0.0
    for r in rows:
        cat = r["user_category"] or r["auto_category"]
        amt = r["amount"]
        bucket = by_cat[cat]
        bucket["n"] += 1
        if amt >= 0:
            bucket["in"] += amt
            total_in += amt
        else:
            bucket["out"] += -amt
            total_out += -amt

    summary = sorted(
        ({"category": k, **v} for k, v in by_cat.items()),
        key=lambda x: x["out"] + x["in"],
        reverse=True,
    )

    return render_template(
        "index.html",
        rows=rows,
        summary=summary,
        categories=ALL_CATEGORIES,
        total_in=total_in,
        total_out=total_out,
        net=total_in - total_out,
        category_filter=category_filter,
        direction=direction,
        search=search,
    )


@app.route("/upload", methods=["POST"])
def upload():
    f = request.files.get("pdf")
    if not f or not f.filename.lower().endswith(".pdf"):
        return redirect(url_for("index"))

    # Save to a temp file because pdfplumber needs a path/file-like object.
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        f.save(tmp.name)
        tmp_path = tmp.name

    try:
        raw = parse_pdf(tmp_path)
    finally:
        os.unlink(tmp_path)

    db = get_db()
    inserted = 0
    for txn in raw:
        cat = categorize(txn.operation, txn.details, txn.amount)
        try:
            db.execute(
                """INSERT INTO txns (fingerprint, date, amount, operation, details, auto_category)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (txn.fingerprint, txn.date, txn.amount, txn.operation, txn.details, cat),
            )
            inserted += 1
        except sqlite3.IntegrityError:
            # Already imported — skip
            continue
    db.commit()

    return redirect(url_for("index", imported=inserted, parsed=len(raw)))


@app.route("/txn/<int:txn_id>", methods=["POST"])
def update_txn(txn_id: int):
    """Update user_category and/or note for a transaction."""
    db = get_db()
    payload = request.get_json(silent=True) or request.form
    category = (payload.get("category") or "").strip() or None
    note = (payload.get("note") or "").strip() or None

    db.execute(
        "UPDATE txns SET user_category = ?, note = ? WHERE id = ?",
        (category, note, txn_id),
    )
    db.commit()

    row = db.execute("SELECT * FROM txns WHERE id = ?", (txn_id,)).fetchone()
    if row is None:
        return jsonify({"ok": False, "error": "not found"}), 404
    return jsonify({
        "ok": True,
        "id": row["id"],
        "category": row["user_category"] or row["auto_category"],
        "note": row["note"] or "",
    })


@app.route("/txn/<int:txn_id>/delete", methods=["POST"])
def delete_txn(txn_id: int):
    db = get_db()
    db.execute("DELETE FROM txns WHERE id = ?", (txn_id,))
    db.commit()
    return jsonify({"ok": True})


@app.route("/reset", methods=["POST"])
def reset():
    db = get_db()
    db.execute("DELETE FROM txns")
    db.commit()
    return redirect(url_for("index"))


@app.route("/export.csv")
def export_csv():
    """Plain CSV export of all transactions with their effective category."""
    import csv
    import io

    db = get_db()
    rows = db.execute("SELECT * FROM txns ORDER BY date, id").fetchall()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["date", "amount", "operation", "details", "category", "note"])
    for r in rows:
        w.writerow([
            r["date"], f"{r['amount']:.2f}", r["operation"], r["details"],
            r["user_category"] or r["auto_category"], r["note"] or "",
        ])
    from flask import Response
    return Response(
        buf.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=kaspi_export.csv"},
    )


if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=5000, debug=True)
