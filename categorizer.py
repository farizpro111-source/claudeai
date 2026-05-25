"""Auto-categorization of Kaspi transactions by merchant / description keywords."""

from __future__ import annotations

import re
from typing import Iterable

# Order matters: first matching category wins. Keywords are case-insensitive
# substrings that we look for in the transaction "details" field.
CATEGORY_RULES: list[tuple[str, list[str]]] = [
    ("Продукты", [
        "magnum", "small", "galmart", "anvar", "smart", "metro",
        "ramstor", "carrefour", "айкын", "арзан", "tomas", "europharma food",
        "пятёрочка", "корзина", "molochka", "продукт",
    ]),
    ("Кафе и рестораны", [
        "kfc", "burger", "mcdonald", "макдональдс", "starbucks", "coffee",
        "кофе", "ресторан", "cafe", "кафе", "pizza", "пицца", "sushi",
        "суши", "хинкали", "del papa", "salam bro", "bahandi", "chocofood",
        "wolt", "glovo", "yandex eda", "яндекс еда",
    ]),
    ("Транспорт и такси", [
        "yandex go", "yandex.go", "яндекс go", "indrive", "indriver",
        "uber", "bolt", "такси", "taxi", "автобус", "metro almaty",
        "onay", "оплата проезда",
    ]),
    ("АЗС и топливо", [
        "helios", "compass", "sinooil", "qazaq oil", "азс", "fuel",
        "petrol", "lukoil", "kazmunaygas", "кмг",
    ]),
    ("Аптеки и здоровье", [
        "europharma", "europhar", "apteka", "аптека", "садыхан", "biosfera",
        "клиника", "медицин", "стоматол", "dent",
    ]),
    ("Маркетплейсы и онлайн", [
        "kaspi shop", "kaspi магазин", "kaspi.shop", "wildberries",
        "wb.kz", "ozon", "lamoda", "aliexpress", "alibaba", "temu",
        "halyk market",
    ]),
    ("Подписки и развлечения", [
        "netflix", "spotify", "youtube", "apple.com/bill", "google",
        "icloud", "ivi", "kinopoisk", "megogo", "kino", "patreon",
        "steam", "playstation", "xbox", "chocofamily",
    ]),
    ("Связь и интернет", [
        "kcell", "activ", "beeline", "tele2", "altel", "izi", "казахтелеком",
        "kazakhtelecom", "transtelecom", "alma tv", "id net",
    ]),
    ("Коммунальные платежи", [
        "квартплата", "коммуналь", "электроэнерг", "kazakhmys energy",
        "alseco", "астанаэнергосбыт", "kazwater", "su arnasy", "тепло",
        "газ", "qazaqgaz",
    ]),
    ("Одежда и обувь", [
        "zara", "h&m", "lc waikiki", "bershka", "pull&bear", "mango",
        "adidas", "nike", "puma", "reebok", "intertop", "respect", "ecco",
        "boutique", "детский мир",
    ]),
    ("Красота и уход", [
        "mon amie", "letoile", "л'этуаль", "yves rocher", "the body shop",
        "салон", "barbershop", "nails", "маникюр", "парикмах",
    ]),
    ("Снятие наличных", [
        "снятие", "atm", "банкомат", "выдача наличных", "cash withdraw",
    ]),
    ("Переводы людям", [
        "перевод клиенту", "перевод физическому", "перевод между",
        "p2p", "перевод на карту", "kaspi gold перевод",
    ]),
    ("Платежи и услуги", [
        "штраф", "налог", "госуслуг", "пошлин", "judiciary", "egov",
    ]),
    ("Образование", [
        "школ", "универ", "kbtu", "naryn", "iitu", "narxoz", "kimep",
        "курс", "udemy", "coursera",
    ]),
]

INCOME_RULES: list[tuple[str, list[str]]] = [
    ("Зарплата", ["зарплат", "salary", "payroll", "оклад"]),
    ("Пополнение", ["пополнен", "top up", "top-up", "deposit", "внесение"]),
    ("Возврат", ["возврат", "refund", "reversal"]),
    ("Входящий перевод", ["перевод", "transfer", "kaspi gold"]),
]


def _match(rules: Iterable[tuple[str, list[str]]], haystack: str) -> str | None:
    h = haystack.lower()
    for category, keywords in rules:
        for kw in keywords:
            if kw in h:
                return category
    return None


def categorize(operation: str, details: str, amount: float) -> str:
    """Return a best-guess category for a transaction.

    `amount` is negative for expenses, positive for income.
    """
    blob = f"{operation} {details}".strip()

    if amount > 0:
        cat = _match(INCOME_RULES, blob)
        return cat or "Прочие поступления"

    cat = _match(CATEGORY_RULES, blob)
    if cat:
        return cat

    # Fallbacks based on operation type only
    op_lower = operation.lower()
    if "снятие" in op_lower or "atm" in op_lower:
        return "Снятие наличных"
    if "перевод" in op_lower:
        return "Переводы людям"
    if "покупк" in op_lower or "оплата" in op_lower:
        return "Прочие покупки"
    return "Без категории"


# Categories the UI shows in the dropdown — auto-derived plus a couple of manual
# ones the rules can't infer (e.g. "Долги").
ALL_CATEGORIES: list[str] = sorted({c for c, _ in CATEGORY_RULES} | {c for c, _ in INCOME_RULES} | {
    "Прочие покупки",
    "Прочие поступления",
    "Без категории",
    "Долги (мне должны)",
    "Долги (я должен)",
    "Не мои траты",
    "Подарки",
    "Семья",
    "Здоровье",
    "Накопления",
})
