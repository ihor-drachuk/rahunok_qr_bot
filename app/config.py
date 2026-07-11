import os
from dataclasses import dataclass

MAX_TOKENS = 16000
GATE_MODEL = "claude-haiku-4-5"
GATE_MAX_TOKENS = 1024
ANTHROPIC_TIMEOUT_SECONDS = 120.0
TELEGRAM_MAX_DOWNLOAD_BYTES = 20 * 1024 * 1024  # Telegram Bot API file download limit (20 MB)

# Extraction/validation model, selectable via RAHUNOK_QR_BOT_MODEL. The gate always runs on Haiku.
_MODEL_CHOICES = {
    "haiku": "claude-haiku-4-5",
    "sonnet": "claude-sonnet-5",
    "opus": "claude-opus-4-7",
}
_DEFAULT_MODEL_CHOICE = "opus"


@dataclass(frozen=True)
class Config:
    telegram_bot_token: str
    anthropic_api_key: str
    model: str


def _resolve_model(override: str | None) -> str:
    choice = (override or os.environ.get("RAHUNOK_QR_BOT_MODEL", _DEFAULT_MODEL_CHOICE)).strip().lower()
    if choice not in _MODEL_CHOICES:
        raise RuntimeError(
            f"Invalid model choice {choice!r}; expected one of {', '.join(_MODEL_CHOICES)}")
    return _MODEL_CHOICES[choice]


def load_config(model_override: str | None = None) -> Config:
    token = os.environ.get("RAHUNOK_QR_BOT_TELEGRAM_TOKEN")
    api_key = os.environ.get("RAHUNOK_QR_BOT_ANTHROPIC_API_KEY")
    missing = [name for name, value in (("RAHUNOK_QR_BOT_TELEGRAM_TOKEN", token),
                                        ("RAHUNOK_QR_BOT_ANTHROPIC_API_KEY", api_key)) if not value]
    if missing:
        raise RuntimeError(f"Missing required environment variables: {', '.join(missing)}")
    return Config(telegram_bot_token=token, anthropic_api_key=api_key, model=_resolve_model(model_override))
