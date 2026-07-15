import pytest

from app.config import load_config

ENV = {
    "RAHUNOK_QR_BOT_TELEGRAM_TOKEN": "token",
    "RAHUNOK_QR_BOT_ANTHROPIC_API_KEY": "key",
}


def _set_env(monkeypatch, **extra):
    for name, value in {**ENV, **extra}.items():
        monkeypatch.setenv(name, value)
    for name in ("RAHUNOK_QR_BOT_MODEL", "RAHUNOK_QR_BOT_STAGE"):
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


def test_stage_mode_defaults_off(monkeypatch):
    _set_env(monkeypatch)
    assert load_config().stage_mode is False


@pytest.mark.parametrize("value,expected", [
    ("1", True), ("true", True), ("YES", True), (" on ", True),
    ("0", False), ("false", False), ("no", False), ("off", False), ("", False),
])
def test_stage_mode_env_values(monkeypatch, value, expected):
    _set_env(monkeypatch, RAHUNOK_QR_BOT_STAGE=value)
    assert load_config().stage_mode is expected


def test_invalid_stage_value_raises(monkeypatch):
    _set_env(monkeypatch, RAHUNOK_QR_BOT_STAGE="maybe")
    with pytest.raises(RuntimeError, match="RAHUNOK_QR_BOT_STAGE"):
        load_config()


@pytest.mark.parametrize("env_value,override", [("0", True), ("1", False)])
def test_stage_cli_override_beats_env(monkeypatch, env_value, override):
    _set_env(monkeypatch, RAHUNOK_QR_BOT_STAGE=env_value)
    assert load_config(stage_override=override).stage_mode is override


def test_missing_required_vars_raise(monkeypatch):
    monkeypatch.delenv("RAHUNOK_QR_BOT_TELEGRAM_TOKEN", raising=False)
    monkeypatch.delenv("RAHUNOK_QR_BOT_ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="RAHUNOK_QR_BOT_TELEGRAM_TOKEN"):
        load_config()
