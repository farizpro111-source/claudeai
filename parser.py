"""Парсер PDF выписки Kaspi Bank.

Kaspi отдаёт выписку в виде PDF с табличной структурой. Формат не зафиксирован
официально, поэтому парсер устойчиво работает по тексту: ищем строки, начинающиеся
с даты, и достаём из них дату, сумму (со знаком), тип операции и описание.

Если у вас PDF в немного другом формате — поправьте регексы тут, всё остальное
приложение от этого не зависит.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

import pdfplumber


DATE_RE = re.compile(r"\b(\d{2}\.\d{2}\.\d{2,4})\b")
# Сумма Kaspi: "- 1 500,00 ₸" / "+5 000,00 ₸" / "1500.00 KZT". Принимаем разные варианты.
AMOUNT_RE = re.compile(
    r"([+\-−–]?\s?\d[\d\s ]*(?:[.,]\d{2})?)\s?(?:₸|тг|KZT|тенге)",
    re.IGNORECASE,
)
# Известные типы операций Kaspi, какими они встречаются в PDF.
TYPE_KEYWORDS = [
    "Покупка", "Перевод", "Пополнение", "Снятие", "Возврат",
    "Платёж", "Платеж", "Оплата", "Списание", "Зачисление",
]


@dataclass
class Transaction:
    """Одна транзакция, готовая к вставке в БД."""
    date: str           # ISO YYYY-MM-DD
    raw_date: str       # как было в PDF
    tx_type: str        # "Покупка", "Перевод", ...
    amount: float       # отрицательная — расход, положительная — приход
    description: str    # получатель / комментарий
    fingerprint: str    # стабильный хэш, чтобы не дублировать при повторной загрузке


def _parse_amount(raw: str) -> float | None:
    """'- 1 500,00' -> -1500.0. Возвращает None, если не разобрать."""
    s = raw.replace(" ", " ").replace(" ", "")
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


def _detect_type(line: str) -> str:
    for kw in TYPE_KEYWORDS:
        if kw.lower() in line.lower():
            return kw
    return "Операция"


def _fingerprint(date: str, amount: float, description: str) -> str:
    h = hashlib.sha1()
    h.update(f"{date}|{amount:.2f}|{description.strip().lower()}".encode("utf-8"))
    return h.hexdigest()


def _extract_text_lines(pdf_path: str) -> list[str]:
    """Достаёт все строки из PDF. Использует pdfplumber, чтобы сохранить порядок."""
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
    """Главная функция: возвращает список транзакций из PDF.

    Алгоритм: идём по строкам, на каждой строке с датой и суммой собираем
    транзакцию. Описание = всё остальное, очищенное от даты/суммы/типа.
    """
    transactions: list[Transaction] = []
    lines = _extract_text_lines(pdf_path)

    # Часто описание переноситcя на следующую строку. Склеиваем строку без даты
    # с предыдущей строкой-транзакцией.
    buffer: list[str] = []
    for line in lines:
        if DATE_RE.search(line) and AMOUNT_RE.search(line):
            if buffer:
                # дополним описание предыдущей транзакции
                if transactions:
                    transactions[-1].description = (
                        transactions[-1].description + " " + " ".join(buffer)
                    ).strip()
                buffer = []

            date_match = DATE_RE.search(line)
            amount_match = AMOUNT_RE.search(line)
            raw_date = date_match.group(1)
            iso_date = _parse_date(raw_date)
            if not iso_date:
                continue
            amount = _parse_amount(amount_match.group(1))
            if amount is None:
                continue
            tx_type = _detect_type(line)

            description = line
            description = DATE_RE.sub("", description, count=1)
            description = AMOUNT_RE.sub("", description, count=1)
            for kw in TYPE_KEYWORDS:
                description = re.sub(re.escape(kw), "", description, count=1, flags=re.IGNORECASE)
            description = re.sub(r"\s+", " ", description).strip(" -–—•|")

            tx = Transaction(
                date=iso_date,
                raw_date=raw_date,
                tx_type=tx_type,
                amount=amount,
                description=description,
                fingerprint=_fingerprint(iso_date, amount, description),
            )
            transactions.append(tx)
        else:
            # строка без даты — возможно продолжение описания
            if transactions and not DATE_RE.search(line):
                buffer.append(line)

    # сольём хвостовой буфер в последнюю транзакцию
    if buffer and transactions:
        transactions[-1].description = (
            transactions[-1].description + " " + " ".join(buffer)
        ).strip()
        # пересчёт fingerprint, т.к. описание изменилось
        transactions[-1].fingerprint = _fingerprint(
            transactions[-1].date,
            transactions[-1].amount,
            transactions[-1].description,
        )

    return transactions
