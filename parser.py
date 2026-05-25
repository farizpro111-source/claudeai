"""Парсер PDF выписки Kaspi Bank.

Формат строки транзакции у Kaspi:
    DD.MM.YY  - 2 420,00 ₸  Покупка YANDEX.GO
    DD.MM.YY  + 15 500,00 ₸ Пополнение Султанмурат К.
    DD.MM.YY  - 4 757,44 ₸ Покупка hostinger.com
                            (- 9,99 USD)          <-- продолжение, без даты

В выписке также есть служебные строки (остатки, итоги по категориям, заголовки),
которые тоже содержат дату и сумму — их надо отсеять, иначе они попадают
в список как «приходы».
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime

import pdfplumber


# Дата в формате DD.MM.YY или DD.MM.YYYY в начале строки.
DATE_RE = re.compile(r"^\s*(\d{2}\.\d{2}\.\d{2,4})\b")
# Сумма Kaspi: "- 1 500,00 ₸", "+5 000,00 ₸", "1500.00 KZT".
AMOUNT_RE = re.compile(
    r"([+\-−–]?\s?\d[\d\s ]*(?:[.,]\d{2})?)\s?(?:₸|тг|KZT|тенге)",
    re.IGNORECASE,
)

# Эти ключевые слова означают, что строка — реальная транзакция.
# Если ни одно не встретилось, строку игнорируем (это служебный остаток/итог).
TX_TYPES = [
    "Покупка", "Перевод", "Пополнение", "Снятие", "Возврат",
    "Платёж", "Платеж", "Оплата", "Списание", "Зачисление",
]

# Подстроки, при которых строку точно нужно пропустить, даже если в ней
# есть дата и сумма (это сводки, остатки, заголовки страниц и т.п.).
SKIP_LINE_MARKERS = [
    "Доступно на",
    "Остаток",
    "Итого",
    "Лимит",
    "Краткое содержание",
    "Эквивалент",
    "Сумма на счете",
    "Валюта счета",
    "ВЫПИСКА",
    "Приложение к Справке",
    "СПРАВКА",
    "Номер счета",
    "Номер карты",
    # Футеры/заголовки страниц.
    "Kaspi Bank",
    "www.kaspi.kz",
    "БИК CASPKZKA",
    "содержит информацию об операциях",
    "между счетами",
    "Раздел",
]


@dataclass
class Transaction:
    date: str           # ISO YYYY-MM-DD
    raw_date: str
    tx_type: str        # один из TX_TYPES
    amount: float       # отрицательная — расход, положительная — приход
    description: str
    fingerprint: str


def _parse_amount(raw: str) -> float | None:
    s = raw.replace(" ", " ").replace(" ", "")
    s = s.replace("−", "-").replace("–", "-")
    s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def _parse_date(raw: str) -> str | None:
    for fmt in ("%d.%m.%Y", "%d.%m.%y"):
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _detect_type(line: str) -> str | None:
    """Ищем точное слово (тип операции). Если ничего не нашли — None,
    значит это не транзакционная строка, её надо пропустить."""
    for kw in TX_TYPES:
        # \b в середине слова в русском работает плохо, поэтому ищем подстроку
        # с пробелом/началом/концом по краям.
        if re.search(rf"(?:^|\s){re.escape(kw)}(?:\s|$)", line, re.IGNORECASE):
            return kw
    return None


def _should_skip(line: str) -> bool:
    for marker in SKIP_LINE_MARKERS:
        if marker.lower() in line.lower():
            return True
    return False


def _fingerprint(date: str, amount: float, description: str, idx: int) -> str:
    """Хэш для дедупликации. Включает индекс строки, потому что Kaspi
    спокойно содержит несколько одинаковых переводов в один день."""
    h = hashlib.sha1()
    h.update(
        f"{date}|{amount:.2f}|{description.strip().lower()}|{idx}".encode("utf-8")
    )
    return h.hexdigest()


def _extract_text_lines(pdf_path: str) -> list[str]:
    lines: list[str] = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            for line in text.splitlines():
                line = line.strip()
                if line:
                    lines.append(line)
    return lines


def parse_kaspi_pdf(pdf_path: str) -> list[Transaction]:
    transactions: list[Transaction] = []
    lines = _extract_text_lines(pdf_path)

    idx = 0
    for line in lines:
        date_m = DATE_RE.search(line)
        amount_m = AMOUNT_RE.search(line)

        is_transaction_line = (
            date_m is not None
            and amount_m is not None
            and not _should_skip(line)
            and _detect_type(line) is not None
        )

        if is_transaction_line:
            raw_date = date_m.group(1)
            iso_date = _parse_date(raw_date)
            if not iso_date:
                continue
            amount = _parse_amount(amount_m.group(1))
            if amount is None:
                continue
            tx_type = _detect_type(line) or "Операция"

            description = line
            description = DATE_RE.sub("", description, count=1)
            description = AMOUNT_RE.sub("", description, count=1)
            description = re.sub(
                rf"(?:^|\s){re.escape(tx_type)}(?:\s|$)",
                " ",
                description,
                count=1,
                flags=re.IGNORECASE,
            )
            description = re.sub(r"\s+", " ", description).strip(" -–—•|:")

            tx = Transaction(
                date=iso_date,
                raw_date=raw_date,
                tx_type=tx_type,
                amount=amount,
                description=description,
                fingerprint=_fingerprint(iso_date, amount, description, idx),
            )
            transactions.append(tx)
            idx += 1
        else:
            # Строка без даты/типа — возможно, это продолжение описания
            # предыдущей транзакции (например, "(- 9,99 USD)" под покупкой
            # на hostinger.com).
            if (
                transactions
                and not DATE_RE.search(line)
                and not _should_skip(line)
            ):
                tail = line.strip()
                if tail:
                    transactions[-1].description = (
                        transactions[-1].description + " " + tail
                    ).strip()

    return transactions
