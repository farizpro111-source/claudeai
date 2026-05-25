"""Категории расходов/доходов и правила автоматической классификации.

Правила — это список (категория, [ключевые слова]). Поиск регистронезависимый,
по подстроке в описании транзакции (получатель / комментарий).
Можно редактировать прямо здесь, чтобы добавлять/менять категории.
"""

# Базовый список категорий. Добавляйте свои.
CATEGORIES = [
    "Продукты",
    "Кафе и рестораны",
    "Транспорт",
    "Топливо",
    "Связь и интернет",
    "Коммунальные",
    "Здоровье и аптеки",
    "Одежда",
    "Развлечения",
    "Подписки",
    "Снятие наличных",
    "Перевод (входящий)",
    "Перевод (исходящий)",
    "Зарплата / доход",
    "Долги (дал в долг)",
    "Долги (вернули)",
    "Не моё (потрачено за других)",
    "Прочее",
]

# Правила автокатегоризации: первое подходящее совпадение выигрывает.
# Ключевые слова — на русском, казахском и английском, как они появляются в Kaspi PDF.
RULES = [
    ("Продукты", [
        "magnum", "магнум", "small", "смолл", "галмарт", "galmart",
        "anvar", "анвар", "ramstore", "рамстор", "metro", "метро",
        "skif", "скиф", "арзан", "arzan", "city center", "столичный",
        "продукт", "супермаркет", "magazin", "магазин", "carrefour",
    ]),
    ("Кафе и рестораны", [
        "kfc", "macdonald", "mcdonald", "burger", "starbucks", "coffee",
        "кофе", "кафе", "ресторан", "restaurant", "shaurma", "шаурма",
        "domino", "пицца", "pizza", "del papa", "bahandi", "shashlyk",
        "wolt", "glovo", "yandex eda", "yandex.eda", "chocofood",
    ]),
    ("Транспорт", [
        "yandex go", "yandex.go", "indriver", "indrive", "uber",
        "такси", "taxi", "onay", "онай", "автобус", "метро астана",
    ]),
    ("Топливо", [
        "helios", "гелиос", "kazmunaygaz", "кмг", "газпромнефть",
        "azs", "азс", "shell", "lukoil", "qazaqoil", "sinooil", "compass",
    ]),
    ("Связь и интернет", [
        "beeline", "билайн", "tele2", "теле2", "activ", "актив", "kcell",
        "altel", "izet", "алма-тв", "alma tv", "transtelecom",
    ]),
    ("Коммунальные", [
        "kazpost", "казпочта", "коммуналь", "квартплат", "ksk", "кск",
        "электр", "газ ", "vodokanal", "водоканал", "теплосеть",
        "kazenergo", "алсеко", "alseco",
    ]),
    ("Здоровье и аптеки", [
        "europharma", "европтека", "аптек", "apteka", "pharm",
        "olymp", "sadyhan", "садыхан", "стоматолог", "клиника",
        "поликлиника", "больница", "hospital",
    ]),
    ("Одежда", [
        "lc waikiki", "zara", "h&m", "pull&bear", "bershka", "mango",
        "одежда", "ostin", "ostin.com", "uniqlo", "kari", "intertop",
    ]),
    ("Развлечения", [
        "kinopark", "kino", "кинотеатр", "chaplin", "arman", "boom",
        "ivi", "okko", "netflix", "spotify", "youtube premium",
        "playstation", "steam", "xbox", "вечеринк",
    ]),
    ("Подписки", [
        "google", "apple.com/bill", "icloud", "netflix", "spotify",
        "youtube", "yandex plus", "yandex.plus", "chatgpt", "openai",
        "github", "notion", "figma", "jetbrains",
    ]),
    ("Снятие наличных", [
        "снятие", "withdrawal", "atm", "банкомат", "cash",
    ]),
    ("Зарплата / доход", [
        "зарплат", "salary", "оклад", "аванс", "выплат", "пособие",
        "стипенди", "гонорар",
    ]),
]


def auto_categorize(description: str, amount: float) -> str:
    """Возвращает категорию по правилам. Если ничего не подошло — None,
    чтобы вызывающий код мог применить дефолт (перевод/прочее)."""
    if not description:
        return None
    desc = description.lower()
    for category, keywords in RULES:
        for kw in keywords:
            if kw in desc:
                return category
    return None


def default_category(tx_type: str, amount: float) -> str:
    """Дефолт, когда правила не сработали. Опирается на тип операции и знак."""
    t = (tx_type or "").lower()
    if "перевод" in t or "transfer" in t:
        return "Перевод (входящий)" if amount > 0 else "Перевод (исходящий)"
    if amount > 0:
        return "Зарплата / доход"
    return "Прочее"
