import pytest

from app.config import load_config

ENV = {
    "RAHUNOK_QR_BOT_TELEGRAM_TOKEN": "token",
    "RAHUNOK_QR_BOT_ANTHROPIC_API_KEY": "key",
}


def _set_env(monkeypatch, **extra):
    for name, value in {**ENV, **extra}.items():
        monkeypatch.setenv(name, value)
    for name in ("RAHUNOK_QR_BOT_MODEL",):
        if name not in extra:
            monkeypatch.delenv(name, raising=False)


def test_default_model_is_opus(monkeypatch):
    _set_env(monkeypatch)
    assert load_config().model == "claude-opus-4-7"


@pytest.mark.parametrize("choice,expected", [
    ("haiku", "claude-haiku-4-5"),
    ("sonnet", "claude-sonnet-5"),
    ("opus", "claude-opus-4-7"),
    ("OPUS", "claude-opus-4-7"),
    (" opus ", "claude-opus-4-7"),
])
def test_model_choice_resolves(monkeypatch, choice, expected):
    _set_env(monkeypatch, RAHUNOK_QR_BOT_MODEL=choice)
    assert load_config().model == expected


def test_invalid_model_choice_raises(monkeypatch):
    _set_env(monkeypatch, RAHUNOK_QR_BOT_MODEL="gpt")
    with pytest.raises(RuntimeError, match="Invalid model choice"):
        load_config()


def test_cli_override_beats_env(monkeypatch):
    _set_env(monkeypatch, RAHUNOK_QR_BOT_MODEL="haiku")
    assert load_config(model_override="sonnet").model == "claude-sonnet-5"


def test_missing_required_vars_raise(monkeypatch):
    monkeypatch.delenv("RAHUNOK_QR_BOT_TELEGRAM_TOKEN", raising=False)
    monkeypatch.delenv("RAHUNOK_QR_BOT_ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="RAHUNOK_QR_BOT_TELEGRAM_TOKEN"):
        load_config()
