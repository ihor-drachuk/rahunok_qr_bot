"""All Ukrainian user-facing strings and reply formatting."""

import re
from decimal import Decimal

from aiogram import html
from aiogram.types import MessageEntity, User

from app import emoji
from app.models import ExtractedRequisites, PurposeFields

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
    "• Текстове повідомлення з реквізитами\n\n"
    "Питання чи пропозиції — /contact"
)

COMMAND_HELP_DESCRIPTION = "Як користуватися ботом"
COMMAND_CONTACT_DESCRIPTION = "Написати розробнику"

# Status lines are built on demand so the ⚙️ can render as the pack's custom emoji once resolved.
def status_searching() -> str:
    return f"{emoji.render('⚙️')} Шукаю платіжні реквізити…"


def status_extracting() -> str:
    return f"{emoji.render('⚙️')} Розпізнаю реквізити…"


def status_validating() -> str:
    return f"{emoji.render('⚙️')} Звіряю реквізити…"


def status_retrying() -> str:
    return f"{emoji.render('⚙️')} Ще раз все перевіряю…"

ERR_NOT_PAYMENT = (
    "🤷 Я не знайшов тут платіжних реквізитів.\n\n"
    "Я створюю платіжні QR-коди за стандартом НБУ: надішліть рахунок у PDF, скріншот із реквізитами "
    "або текст з IBAN — і я поверну QR-код для оплати у банківському застосунку."
)
ERR_UNSUPPORTED_TYPE = "❌ Такий формат я поки не читаю. Надішліть, будь ласка, PDF, зображення (PNG/JPEG) або текст."
ERR_FILE_TOO_BIG = "❌ Файл завеликий — Telegram дозволяє ботам приймати файли до 20 МБ. Спробуйте стиснути його або надіслати скріншот."
ERR_UNRELIABLE = (
    "❌ Не вдалося надійно розпізнати реквізити: повторна перевірка виявила розбіжності. "
    "Спробуйте надіслати чіткіший документ чи скріншот, або введіть реквізити текстом."
)
ERR_NO_IBAN = (
    "❌ Не вдалося знайти коректний IBAN — без нього QR-код для оплати не створити. "
    "Спробуйте, будь ласка, надіслати чіткіший документ чи скріншот або введіть реквізити текстом."
)
ERR_NAME_TOO_LONG = (
    "❌ Назва отримувача задовга для QR-коду НБУ. "
    "Спробуйте, будь ласка, ввести реквізити текстом зі скороченою назвою."
)
ERR_RATE_LIMIT = "😔 Сервіс розпізнавання зараз перевантажений. Спробуйте, будь ласка, пізніше."
ERR_NETWORK = "😔 Не вдалося звʼязатися із сервісом розпізнавання. Спробуйте, будь ласка, ще раз або пізніше."
ERR_API = "😔 Сервіс розпізнавання тимчасово не відповідає. Спробуйте, будь ласка, трохи пізніше."
ERR_TELEGRAM = "⚠️ Помилка Telegram під час обробки повідомлення. Спробуйте ще раз."
ERR_UNEXPECTED = "😔 Сталася неочікувана помилка. Спробуйте, будь ласка, ще раз пізніше."
ERR_NO_INVOICE_REFERENCE = (
    "🤷 У документі немає ні номера, ні дати рахунку, тому скласти призначення платежу не вдалося. "
    "Введіть його вручну у застосунку банку."
)
ERR_PURPOSE_UNRELIABLE = (
    "❌ Не вдалося надійно скласти призначення платежу: повторна перевірка виявила розбіжності. "
    "Введіть його вручну у застосунку банку."
)
ERR_REQUISITES_CHANGED = (
    "❌ Реквізити в документі не збіглися з цією карткою — можливо, повідомлення змінили. "
    "Надішліть, будь ласка, документ ще раз."
)

WARN_NO_AMOUNT = "суму не знайдено — введіть її вручну у застосунку банку"
WARN_BAD_AMOUNT = "суму не вдалося розібрати — введіть її вручну у застосунку банку"
WARN_AMOUNT_TOO_LARGE = "сума перевищує ліміт QR-коду НБУ — введіть її вручну у застосунку банку"
WARN_NO_NAME = "назву отримувача не знайдено"
WARN_NO_CODE = "код ЄДРПОУ/РНОКПП не знайдено"
WARN_BAD_CODE = (
    "код ЄДРПОУ/РНОКПП має нетиповий формат, тому його не додано до QR-коду — перевірте його та введіть вручну"
)
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


def parse_requisites(text: str, entities: list[MessageEntity]) -> ExtractedRequisites | None:
    """Reads back the values that `format_requisites` wrote, from the `text` and `entities` Telegram gives for
    the sent message. `None` when the text holds no IBAN."""
    fields_by_label = {f"{label}: ": field for field, label in _FIELD_LABELS}
    utf16 = text.encode("utf-16-le")
    values = {}
    for entity in entities:
        if entity.type != "code":
            continue
        before = utf16[:entity.offset * 2].decode("utf-16-le")  # entity offsets count UTF-16 code units
        field = fields_by_label.get(before[before.rfind("\n") + 1:])
        if field is not None and field not in values:
            values[field] = entity.extract_from(text)
    return ExtractedRequisites(**values) if "iban" in values else None


def format_generated_purpose(fields: PurposeFields) -> str:
    """Fills the purpose template from `fields`. `fields` must hold `invoice_number` or `invoice_date`, otherwise the
    purpose does not say which invoice it pays."""
    if fields.item_name:
        subject = f'"{fields.item_name}"'
    else:
        subject = "послуги" if fields.goods_kind == "services" else "товар"
    reference = "згідно рахунку"
    if fields.invoice_number:
        reference += f" №{fields.invoice_number}"
    if fields.invoice_date:
        reference += f" від {fields.invoice_date}"
    return f"Оплата за {subject} {reference}"


def format_pay_links(qr) -> str:
    # Tappable links; on a phone they open the payment in a bank app with requisites pre-filled.
    pay = html.link(PAY_LABEL, qr.url)
    mono = html.link(PAY_MONO_LABEL, qr.mono_url)
    return f"{PAY_INTRO}\n{pay}   ·   {mono}"


def format_success(requisites, warnings: list[str], qr) -> str:
    text = f"{emoji.render('🧾')} Реквізити платежу:\n" + format_requisites(requisites)
    if warnings:
        text += "\n\n📝 Що варто перевірити:\n" + "\n".join(f"• {w}" for w in warnings)
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


def contact_prompt(minutes: int) -> str:
    return (
        "✉️ Напишіть повідомлення для розробника — можна з фото, файлами чи альбомом. "
        f"Я передам наступне ваше повідомлення, надіслане протягом {minutes} хв."
    )


CONTACT_CANCEL_LABEL = "Скасувати"
CONTACT_CANCELLED = "Добре, скасовано."
CONTACT_NOTHING_TO_CANCEL = "Немає чого скасовувати."
FEEDBACK_SENT = "✅ Дякую! Передав розробнику."
FEEDBACK_COOLDOWN = "⏳ Зачекайте, будь ласка, пів хвилини й надішліть /contact ще раз."
FEEDBACK_FAILED = "❌ Не вдалося переслати таке повідомлення. Спробуйте ще раз через /contact — текстом або іншим файлом."
REPORT_COOLDOWN = "⏳ Зачекайте, будь ласка, пів хвилини й спробуйте ще раз."

REPORT_DOCUMENT_LABEL = "📤 Надіслати документ розробнику"
REPORT_MESSAGE_LABEL = "📤 Надіслати повідомлення розробнику"
REPORT_CONFIRM_LABEL = "✅ Так, надіслати"
REPORT_DECLINE_LABEL = "✖️ Не треба"
REPORT_SENT = "✅ Надіслано розробнику"
REPORT_FAILED = "Не вдалося надіслати. Спробуйте, будь ласка, пізніше."
SOURCE_GONE = "Не вдалося: повідомлення вже видалено"
MESSAGE_EXPIRED = "Це повідомлення вже застаріло"

PURPOSE_LABEL = "✍️ Згенерувати призначення"
PURPOSE_IN_PROGRESS_LABEL = "⏳ Генерую…"
PURPOSE_ALREADY_RUNNING = "⏳ Уже генерую, зачекайте трохи."

DEVELOPER_REPLY_INTRO = "💬 Це відповідь розробника. Щоб відповісти, надішліть /contact."
DEVELOPER_REPLY_HINT = "↩️ Щоб відповісти, зробіть Reply саме на заголовок з ID."
DEVELOPER_REPLY_DELIVERED = "✅ Доставлено"
DEVELOPER_REPLY_BLOCKED = "❌ Не доставлено: користувач заблокував бота"
DEVELOPER_REPLY_FAILED = "❌ Не доставлено: помилка Telegram"

_FEEDBACK_HEADER_ID = re.compile(r"^ID: (\d+) · ([0-9a-f]+)$", re.MULTILINE)


def format_report_sent(reply_html: str) -> str:
    return f"{reply_html}\n\n{REPORT_SENT}"


def format_feedback_header(user: User, signature: str) -> str:
    return _feedback_header("📨 Повідомлення від користувача", user, signature, [])


def format_report_header(user: User, signature: str, error: str, is_text: bool) -> str:
    title = "📨 Повідомлення, яке бот не розпізнав" if is_text else "📨 Документ, який бот не розпізнав"
    return _feedback_header(title, user, signature, [f"Помилка: {html.quote(error)}"])


def _feedback_header(title: str, user: User, signature: str, extra_lines: list[str]) -> str:
    # We put the ID line before the name and the error text, so that `parse_feedback_address` finds it first,
    # even when a later line looks like one.
    sender = html.quote(user.full_name) + (f" (@{user.username})" if user.username else "")
    id_line = f"ID: {html.code(str(user.id))} · {html.code(signature)}"
    lines = [title, id_line, f"Від: {sender}", *extra_lines, "↩️ Reply на це повідомлення — відповідь отримає користувач."]
    return "\n".join(lines)


def parse_feedback_address(header_text: str) -> tuple[int, str] | None:
    match = _FEEDBACK_HEADER_ID.search(header_text)
    return (int(match.group(1)), match.group(2)) if match else None
