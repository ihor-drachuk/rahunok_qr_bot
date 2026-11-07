<div align="center">
  <img src="assets/logo-gh.png" alt="rahunok_qr_bot logo" width="120"/>

  <h3>Рахунок&nbsp;→&nbsp;QR</h3>
  <p><b>Telegram-бот для зручної оплати</b></p>

  <p><i>Telegram bot (<a href="https://t.me/rahunok_qr_bot"><code>@rahunok_qr_bot</code></a>) that turns Ukrainian payment requisites into NBU-standard payment QR codes</i></p>

  [![CI](https://github.com/ihor-drachuk/rahunok_qr_bot/actions/workflows/ci.yml/badge.svg)](https://github.com/ihor-drachuk/rahunok_qr_bot/actions/workflows/ci.yml)
  ![Python](https://img.shields.io/badge/python-3.12+-blue)
</div>

Send the bot a PDF invoice, a screenshot, or plain text with requisites — it extracts them with the Anthropic API, builds a `https://bank.gov.ua/qr/...` payment QR, and replies with the QR image plus the recognized requisites so you can copy them manually. Scanning the QR in Monobank, Privat24, or any other Ukrainian banking app pre-fills the "Оплата за IBAN" form: recipient, IBAN, EDRPOU/RNOKPP, amount, and payment purpose.

## How it works

1. **Gate** — a cheap `claude-haiku-5-5` call checks the message plausibly contains payment requisites at all; casual chat and unrelated files are politely turned away without running the expensive models.
2. **Extract** — the extraction model (`claude-opus-5-5` by default, see `RAHUNOK_QR_BOT_MODEL`) reads the PDF/image/text and returns structured requisites (the payment purpose is captured in full).
3. **Verify** — an independent second call checks every extracted field against the source; on a mismatch the extraction is retried once, and a second mismatch aborts with an error instead of guessing.
4. **Deterministic checks** — IBAN MOD-97 checksum (hard gate: no QR without a valid IBAN), EDRPOU/RNOKPP shape, amount parsing.
5. **QR** — the NBU payment QR is built by [`nbu_payment_qr`](https://github.com/ihor-drachuk/nbu_payment_qr) with error correction H, so the logo on the reply card does not break scanning.

While the pipeline runs, the bot shows a live status message and removes it once the reply is ready. The ⚙️ in the status lines and the 🧾 in the result are custom emoji from the bot's own [emoji pack](https://t.me/addemoji/rahunok_qr_emoji), resolved at startup with a graceful fallback to plain emoji if a lookup fails.

Missing optional fields, a malformed amount or code, and an amount above the QR limit are left out of the QR, with a warning. Without an amount, the banking app asks the payer to enter it. A recipient name too long for the QR ends with an error instead of a QR. The bot replies in Ukrainian.

## Feedback

`/contact` and its alias `/feedback` send the user's next message, or album, to the feedback chat, if the user sends it within 15 minutes. `/contact <text>`, or `/contact` as the caption of a photo, file or album, sends that message at once.

When the bot cannot recognize a document or a text, its error reply gets a "📤 Надіслати документ розробнику" or "📤 Надіслати повідомлення розробнику" button. After a confirmation, the button sends the original message to the feedback chat.

The bot copies messages with `copyMessages`, so it never downloads attachments. The developer answers a user by replying to the header message that carries the user's ID.

## Supported inputs

| Input | Details |
|---|---|
| PDF document | invoices, bills (up to 20 MB — Telegram bot download limit) |
| Image | PNG/JPEG, sent as photo or as file |
| Text | any message containing requisites |

## Configuration

| Variable | Description |
|---|---|
| `RAHUNOK_QR_BOT_TELEGRAM_TOKEN` | Bot token from [@BotFather](https://t.me/BotFather) |
| `RAHUNOK_QR_BOT_ANTHROPIC_API_KEY` | Anthropic API key |
| `RAHUNOK_QR_BOT_FEEDBACK_CHAT_ID` | Chat that receives `/contact` messages and failed documents: a user ID, or a negative group ID. The bot does not start without it, or when it cannot reach that chat, so first press `/start` in the bot from that account, or add the bot to that group. Not used by the CLI. |
| `RAHUNOK_QR_BOT_MODEL` | Optional: extraction/validation model — `haiku`, `sonnet`, or `opus` (default `opus`). The gate always runs on Haiku. |
| `RAHUNOK_QR_BOT_STAGE` | Optional: stage mode — `true`/`false` (default `false`). Marks a staging instance by compositing a hammer overlay onto the reply-card logos. |

## Run locally

```sh
git clone --recurse-submodules https://github.com/ihor-drachuk/rahunok_qr_bot.git
cd rahunok_qr_bot
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt   # Linux/macOS: .venv/bin/pip
set RAHUNOK_QR_BOT_TELEGRAM_TOKEN=...           # Linux/macOS: export
set RAHUNOK_QR_BOT_ANTHROPIC_API_KEY=...
set RAHUNOK_QR_BOT_FEEDBACK_CHAT_ID=...
python -m app.main
```

## Deploy to Railway

1. Create a new Railway service from this repository — the `Dockerfile` is picked up automatically.
2. Set `RAHUNOK_QR_BOT_TELEGRAM_TOKEN`, `RAHUNOK_QR_BOT_ANTHROPIC_API_KEY` and `RAHUNOK_QR_BOT_FEEDBACK_CHAT_ID` in the service Variables.
3. Deploy. The bot is a long-polling worker: no public URL, no `PORT`. Keep it at a **single replica** — two pollers on one bot token conflict.

## Diagnostic CLI

Run the pipeline on a file or text without Telegram — prints the extracted requisites and the decoded QR payload, optionally saving the QR and reply-card PNGs. Uses the same env vars as the bot, except `RAHUNOK_QR_BOT_FEEDBACK_CHAT_ID`.

```sh
python -m app.cli invoice.pdf                     # or a .png/.jpg screenshot
python -m app.cli --text "IBAN UA..., сума 100 грн"
python -m app.cli invoice.pdf --model haiku --qr-out qr.png
python -m app.cli invoice.pdf --stage --card-out card.png   # preview the stage-mode card
```

## Tests

Offline, no tokens needed:

```sh
python -m pytest tests/
```

## Limits

- Telegram Bot API allows bots to download files up to 20 MB.
- The NBU QR payload is capped at 331 bytes; an overlong payment purpose is truncated in the QR (the full text is still shown in the reply).
