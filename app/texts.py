"""All Ukrainian user-facing strings and reply formatting."""

from decimal import Decimal

from aiogram import html

CARD_SUBTITLE = "Telegram-бот для зручної оплати"
CARD_CALL_TO_ACTION = "Відкрий банк → Скануй QR-код"

SUCCESS_FOOTER = "Свій QR можна зробити тут: @rahunok_qr_bot"

PAY_INTRO = "Якщо ти вже читаєш це з телефона:"
PAY_LABEL = "💳 Сплатити"
PAY_MONO_LABEL = "🐱 Монобанк"


def format_card_amount(amount: Decimal | None) -> str | None:
    if amount is None:
        return None
    # Thousands grouped with a non-breaking space, always two decimals: 13 727.00 грн
    grouped = f"{amount:,.2f}".replace(",", " ")
    return f"{grouped} грн"

HELP = (
    "Привіт! Я створюю платіжні QR-коди за стандартом НБУ.\n\n"
    "Надішліть мені рахунок у форматі PDF, скріншот із реквізитами або просто текст "
    "із реквізитами — я розпізнаю їх і надішлю QR-код, який можна відсканувати "
    "у Монобанку, Приват24 чи іншому банківському застосунку.\n\n"
    "Підтримувані вхідні дані:\n"
    "• PDF-документ\n"
    "• Фото або зображення (PNG/JPEG)\n"
    "• Текстове повідомлення з реквізитами"
)

PROCESSING = "🔄 Обробляю…"

ERR_NOT_PAYMENT = (
    "🤷 Я не знайшов тут платіжних реквізитів.\n\n"
    "Я створюю платіжні QR-коди за стандартом НБУ: надішліть рахунок у PDF, скріншот із реквізитами "
    "або текст з IBAN — і я поверну QR-код для оплати у банківському застосунку."
)
ERR_UNSUPPORTED_TYPE = "❌ Непідтримуваний тип файлу. Надішліть PDF, зображення (PNG/JPEG) або текст."
ERR_FILE_TOO_BIG = "❌ Файл завеликий: Telegram дозволяє ботам завантажувати файли до 20 МБ."
ERR_UNRELIABLE = (
    "❌ Не вдалося надійно розпізнати реквізити: повторна перевірка виявила розбіжності. "
    "Спробуйте надіслати чіткіший документ чи скріншот, або введіть реквізити текстом."
)
ERR_NO_IBAN = (
    "❌ Не знайдено коректний IBAN — без нього платіжний QR-код неможливий. "
    "Спробуйте надіслати чіткіший документ чи скріншот, або введіть реквізити текстом."
)
ERR_RATE_LIMIT = "⚠️ Сервіс розпізнавання тимчасово перевантажений. Спробуйте ще раз за хвилину."
ERR_NETWORK = "⚠️ Проблема з мережею під час звернення до сервісу розпізнавання. Спробуйте ще раз."
ERR_API = "⚠️ Помилка сервісу розпізнавання. Спробуйте ще раз пізніше."
ERR_TELEGRAM = "⚠️ Помилка Telegram під час обробки повідомлення. Спробуйте ще раз."
ERR_UNEXPECTED = "⚠️ Сталася неочікувана помилка. Спробуйте ще раз пізніше."

WARN_NO_AMOUNT = "суму не знайдено — введіть її вручну у застосунку банку"
WARN_BAD_AMOUNT = "суму не вдалося розібрати — введіть її вручну у застосунку банку"
WARN_NO_NAME = "назву отримувача не знайдено"
WARN_NO_CODE = "код ЄДРПОУ/РНОКПП не знайдено"
WARN_BAD_CODE = "код ЄДРПОУ/РНОКПП має нетиповий формат — перевірте його"
WARN_NO_PURPOSE = "призначення платежу не знайдено"
WARN_TRUNCATED_PURPOSE = (
    "призначення платежу скорочено у QR-коді через ліміт розміру — повний текст наведено вище"
)

_FIELD_LABELS = (
    ("recipient_name", "Отримувач"),
    ("iban", "IBAN"),
    ("edrpou_rnokpp", "ЄДРПОУ/РНОКПП"),
    ("amount", "Сума"),
    ("payment_purpose", "Призначення платежу"),
)


def format_requisites(requisites) -> str:
    lines = []
    for field, label in _FIELD_LABELS:
        value = getattr(requisites, field)
        if value:
            rendered = html.code(html.quote(value))
            if field == "amount":
                rendered += " грн"
            lines.append(f"{label}: {rendered}")
    return "\n".join(lines)


def format_pay_links(qr) -> str:
    # Tappable links; on a phone they open the payment in a bank app with requisites pre-filled.
    pay = html.link(PAY_LABEL, qr.url)
    mono = html.link(PAY_MONO_LABEL, qr.mono_url)
    return f"{PAY_INTRO}\n{pay}   ·   {mono}"


def format_success(requisites, warnings: list[str], qr) -> str:
    text = "✅ Розпізнані реквізити:\n" + format_requisites(requisites)
    if warnings:
        text += "\n\n⚠️ Зверніть увагу:\n" + "\n".join(f"• {w}" for w in warnings)
    text += "\n\n" + format_pay_links(qr)
    text += "\n\n" + SUCCESS_FOOTER
    return text


def format_error(error: str, requisites) -> str:
    text = error
    if requisites is not None:
        found = format_requisites(requisites)
        if found:
            text += "\n\nЗнайдені поля:\n" + found
    return text
