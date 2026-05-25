"""SQLite-хранилище: транзакции, выученные правила, пользовательские категории."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

DB_PATH = Path(__file__).parent / "data" / "kaspi.db"


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS transactions (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                fingerprint     TEXT UNIQUE NOT NULL,
                date            TEXT NOT NULL,
                tx_type         TEXT NOT NULL,
                amount          REAL NOT NULL,
                description     TEXT NOT NULL,
                category        TEXT NOT NULL,
                note            TEXT NOT NULL DEFAULT '',
                user_edited     INTEGER NOT NULL DEFAULT 0,
                created_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_tx_date ON transactions(date);
            CREATE INDEX IF NOT EXISTS idx_tx_category ON transactions(category);

            CREATE TABLE IF NOT EXISTS merchant_rules (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                pattern         TEXT UNIQUE NOT NULL,
                category        TEXT NOT NULL,
                created_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS user_categories (
                name            TEXT PRIMARY KEY,
                created_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """
        )


@contextmanager
def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def insert_transaction(conn, tx, category: str) -> bool:
    cur = conn.execute(
        """
        INSERT OR IGNORE INTO transactions
            (fingerprint, date, tx_type, amount, description, category)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (tx.fingerprint, tx.date, tx.tx_type, tx.amount, tx.description, category),
    )
    return cur.rowcount > 0


def update_transaction(tx_id: int, category: str, note: str) -> None:
    with connect() as conn:
        conn.execute(
            """
            UPDATE transactions
            SET category = ?, note = ?, user_edited = 1
            WHERE id = ?
            """,
            (category, note, tx_id),
        )


def list_transactions(
    category: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    search: str | None = None,
) -> list[sqlite3.Row]:
    query = "SELECT * FROM transactions WHERE 1=1"
    params: list = []
    if category:
        query += " AND category = ?"
        params.append(category)
    if date_from:
        query += " AND date >= ?"
        params.append(date_from)
    if date_to:
        query += " AND date <= ?"
        params.append(date_to)
    if search:
        query += " AND (description LIKE ? OR note LIKE ?)"
        like = f"%{search}%"
        params.extend([like, like])
    query += " ORDER BY date DESC, id DESC"
    with connect() as conn:
        return conn.execute(query, params).fetchall()


def category_totals(date_from: str | None = None, date_to: str | None = None) -> list[sqlite3.Row]:
    query = """
        SELECT
            category,
            SUM(CASE WHEN amount < 0 THEN -amount ELSE 0 END) AS spent,
            SUM(CASE WHEN amount > 0 THEN  amount ELSE 0 END) AS received,
            COUNT(*) AS n
        FROM transactions
        WHERE 1=1
    """
    params: list = []
    if date_from:
        query += " AND date >= ?"
        params.append(date_from)
    if date_to:
        query += " AND date <= ?"
        params.append(date_to)
    query += " GROUP BY category ORDER BY spent DESC, received DESC"
    with connect() as conn:
        return conn.execute(query, params).fetchall()


def upsert_merchant_rule(pattern: str, category: str) -> int:
    """Сохраняем правило «подстрока → категория» и сразу применяем его ко всем
    транзакциям, которые юзер ещё не правил руками. Возвращает количество
    обновлённых транзакций."""
    pattern = pattern.strip().lower()
    if not pattern:
        return 0
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO merchant_rules (pattern, category) VALUES (?, ?)
            ON CONFLICT(pattern) DO UPDATE SET category = excluded.category
            """,
            (pattern, category),
        )
        # Ретроактивно применяем: все транзакции, описание которых содержит
        # подстроку И которые ещё не редактировались юзером, переносим
        # в новую категорию. Исключаем те, которые уже в этой категории,
        # чтобы счётчик updated был осмысленным.
        cur = conn.execute(
            """
            UPDATE transactions
            SET category = ?
            WHERE user_edited = 0
              AND category != ?
              AND LOWER(description) LIKE ?
            """,
            (category, category, f"%{pattern}%"),
        )
        return cur.rowcount


def learned_category(description: str) -> str | None:
    if not description:
        return None
    desc = description.lower()
    with connect() as conn:
        for row in conn.execute("SELECT pattern, category FROM merchant_rules"):
            if row["pattern"] in desc:
                return row["category"]
    return None


def delete_transaction(tx_id: int) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM transactions WHERE id = ?", (tx_id,))


def add_user_category(name: str) -> bool:
    """Возвращает True, если категория действительно добавлена (а не дубль)."""
    name = name.strip()
    if not name:
        return False
    with connect() as conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO user_categories (name) VALUES (?)", (name,)
        )
        return cur.rowcount > 0


def delete_user_category(name: str) -> None:
    """Удаляет пользовательскую категорию. Транзакции, висевшие в ней,
    остаются — их категория тоже стирается до 'Прочее'."""
    with connect() as conn:
        conn.execute("DELETE FROM user_categories WHERE name = ?", (name,))
        conn.execute(
            "UPDATE transactions SET category = 'Прочее' WHERE category = ?",
            (name,),
        )


def list_user_categories() -> list[str]:
    with connect() as conn:
        return [r["name"] for r in conn.execute(
            "SELECT name FROM user_categories ORDER BY name"
        )]


def list_merchant_rules() -> list[sqlite3.Row]:
    with connect() as conn:
        return conn.execute(
            "SELECT id, pattern, category FROM merchant_rules ORDER BY pattern"
        ).fetchall()


def delete_merchant_rule(rule_id: int) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM merchant_rules WHERE id = ?", (rule_id,))
