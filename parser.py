"""Parser for Kaspi Bank PDF statements.

Kaspi PDF statements (Выписка) come in a handful of layouts depending on the
account type (Gold, Red, Deposit) and the period of issuance, but they all
share a common shape: a transactions table with columns roughly equivalent to

    Дата | Сумма | Операция | Детали

This module extracts that table as plain rows. It is intentionally permissive
- if a row doesn't look like a transaction it's skipped silently.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

import pdfplumber


DATE_RE = re.compile(r"^\d{2}\.\d{2}\.\d{2,4}$")
# Amount looks like "−12 345,67 ₸" / "+1 500,00 ₸" / "12345.67". The minus sign
# in Kaspi PDFs is sometimes the unicode U+2212 (−) rather than ASCII "-".
AMOUNT_RE = re.compile(
    r"^([+\-−])?\s*([0-9][0-9\s ]*[.,]?\d*)\s*(?:₸|тг|kzt)?$",
    re.IGNORECASE,
)


@dataclass
class RawTxn:
    date: str          # ISO date YYYY-MM-DD
    amount: float      # negative = expense, positive = income
    operation: str     # short operation name ("Покупка", "Перевод", ...)
    details: str       # everything else from the row (merchant, comment, etc.)

    @property
    def fingerprint(self) -> str:
        """Stable hash used to de-duplicate when the same PDF is uploaded twice."""
        payload = f"{self.date}|{self.amount:.2f}|{self.operation}|{self.details}"
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _parse_amount(token: str) -> float | None:
    token = token.strip().replace(" ", " ")
    m = AMOUNT_RE.match(token)
    if not m:
        return None
    sign_ch = m.group(1)
    body = m.group(2).replace(" ", "").replace(",", ".")
    try:
        value = float(body)
    except ValueError:
        return None
    if sign_ch in ("-", "−"):
        value = -value
    return value


def _parse_date(token: str) -> str | None:
    token = token.strip()
    for fmt in ("%d.%m.%Y", "%d.%m.%y"):
        try:
            return datetime.strptime(token, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _iter_lines(pdf_path: str) -> Iterable[str]:
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text(x_tolerance=2, y_tolerance=3) or ""
            for line in text.splitlines():
                line = line.strip()
                if line:
                    yield line


def parse_pdf(pdf_path: str) -> list[RawTxn]:
    """Extract every transaction-looking row from a Kaspi PDF."""
    txns: list[RawTxn] = []

    for line in _iter_lines(pdf_path):
        # Expect a date as the first token.
        parts = line.split()
        if not parts or not DATE_RE.match(parts[0]):
            continue
        iso_date = _parse_date(parts[0])
        if not iso_date:
            continue

        # Find the amount. Kaspi typically puts the amount as the 2nd column,
        # but the way pdfplumber re-flows text the amount might span 2-3
        # tokens (e.g. "−12 345,67 ₸"). Greedy scan: try to glue 1..4 tokens
        # together starting from index 1 and pick the longest that parses.
        amount: float | None = None
        amount_end_idx = -1
        for end in range(min(len(parts), 6), 1, -1):
            candidate = "".join(parts[1:end]).replace(" ", "")
            val = _parse_amount(candidate)
            if val is not None and abs(val) >= 1:  # require ≥1 ₸ to avoid noise
                amount = val
                amount_end_idx = end
                break
        if amount is None:
            continue

        rest = parts[amount_end_idx:]
        if not rest:
            continue

        # Operation = first 1-3 tokens that look like a label, details = the rest.
        # Heuristic: operation is everything up to the first lowercase token or
        # bracketed string; fallback to the first 2 tokens.
        operation_tokens: list[str] = []
        details_tokens: list[str] = []
        split_done = False
        for i, tok in enumerate(rest):
            if not split_done and (i < 3) and tok and (tok[0].isupper() or tok[0].isalpha()):
                operation_tokens.append(tok)
                if i >= 1 and tok.endswith((".", ",")):
                    split_done = True
            else:
                split_done = True
                details_tokens.append(tok)

        if not operation_tokens:
            operation_tokens = rest[:1]
            details_tokens = rest[1:]
        operation = " ".join(operation_tokens).strip(" ,.;")
        details = " ".join(details_tokens).strip()

        txns.append(
            RawTxn(
                date=iso_date,
                amount=amount,
                operation=operation,
                details=details,
            )
        )

    return txns
