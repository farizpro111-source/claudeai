"""Веб-приложение для разбора Kaspi PDF выписки.

Запуск:
    pip install -r requirements.txt
    python app.py

Откройте http://127.0.0.1:5000 и загрузите PDF выписку.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from flask import (
    Flask, redirect, render_template, request, url_for, flash, jsonify
)

import db
from categories import CATEGORIES, auto_categorize, default_category
from parser import parse_kaspi_pdf


app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET", "kaspi-local-dev")
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024  # 20 МБ хватит любой выписке


@app.before_request
def _ensure_db():
    db.init_db()


def _pick_category(tx) -> str:
    """Сначала смотрим в выученные правила (что юзер уже размечал),
    потом — статические правила из categories.py, потом — дефолт по типу."""
    learned = db.learned_category(tx.description)
    if learned:
        return learned
    auto = auto_categorize(tx.description, tx.amount)
    if auto:
        return auto
    return default_category(tx.tx_type, tx.amount)


@app.route("/")
def index():
    category = request.args.get("category") or None
    date_from = request.args.get("from") or None
    date_to = request.args.get("to") or None
    search = request.args.get("q") or None

    rows = db.list_transactions(
        category=category, date_from=date_from, date_to=date_to, search=search
    )
    totals = db.category_totals(date_from=date_from, date_to=date_to)

    total_spent = sum(r["spent"] or 0 for r in totals)
    total_received = sum(r["received"] or 0 for r in totals)

    return render_template(
        "index.html",
        transactions=rows,
        totals=totals,
        total_spent=total_spent,
        total_received=total_received,
        categories=CATEGORIES,
        filters={"category": category or "", "from": date_from or "",
                 "to": date_to or "", "q": search or ""},
    )


@app.route("/upload", methods=["POST"])
def upload():
    file = request.files.get("pdf")
    if not file or not file.filename:
        flash("Выберите PDF файл", "error")
        return redirect(url_for("index"))

    if not file.filename.lower().endswith(".pdf"):
        flash("Нужен PDF", "error")
        return redirect(url_for("index"))

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        file.save(tmp.name)
        tmp_path = tmp.name

    try:
        txs = parse_kaspi_pdf(tmp_path)
    except Exception as exc:
        flash(f"Не удалось разобрать PDF: {exc}", "error")
        return redirect(url_for("index"))
    finally:
        try:
            Path(tmp_path).unlink()
        except OSError:
            pass

    if not txs:
        flash("В PDF не нашлось транзакций. Проверьте, что это выписка Kaspi.", "error")
        return redirect(url_for("index"))

    added = 0
    skipped = 0
    with db.connect() as conn:
        for tx in txs:
            cat = _pick_category(tx)
            if db.insert_transaction(conn, tx, cat):
                added += 1
            else:
                skipped += 1

    flash(
        f"Загружено {added} новых транзакций, пропущено дублей: {skipped}.",
        "success",
    )
    return redirect(url_for("index"))


@app.route("/tx/<int:tx_id>/update", methods=["POST"])
def update_tx(tx_id: int):
    category = request.form.get("category", "Прочее").strip()
    note = request.form.get("note", "").strip()
    remember = request.form.get("remember") == "on"
    pattern = request.form.get("remember_pattern", "").strip()

    if category not in CATEGORIES:
        # позволим пользователю добавить новую категорию налету через примечание
        CATEGORIES.append(category)

    db.update_transaction(tx_id, category, note)

    if remember and pattern:
        db.upsert_merchant_rule(pattern, category)
        flash(
            f"Запомнил: «{pattern}» → {category}. Будущие такие транзакции "
            f"попадут в эту категорию автоматически.",
            "success",
        )

    return redirect(request.referrer or url_for("index"))


@app.route("/tx/<int:tx_id>/delete", methods=["POST"])
def delete_tx(tx_id: int):
    db.delete_transaction(tx_id)
    flash("Транзакция удалена", "success")
    return redirect(request.referrer or url_for("index"))


@app.route("/api/totals")
def api_totals():
    """JSON со сводкой по категориям — на случай если захотите делать графики."""
    date_from = request.args.get("from") or None
    date_to = request.args.get("to") or None
    rows = db.category_totals(date_from=date_from, date_to=date_to)
    return jsonify([dict(r) for r in rows])


if __name__ == "__main__":
    db.init_db()
    app.run(host="127.0.0.1", port=5000, debug=True)
