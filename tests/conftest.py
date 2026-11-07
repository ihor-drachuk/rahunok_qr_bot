import pytest

from app import albums, feedback
from app.main import build_dispatcher
from tests.telegram_fakes import DEVELOPER_CHAT_ID


@pytest.fixture(scope="session")
def _shared_dispatcher():
    # aiogram lets a router join only one dispatcher, and `handlers.router` is a module-level object,
    # so we build the real dispatcher once and give each test a clean state instead.
    return build_dispatcher(stage_mode=True, feedback_chat_id=DEVELOPER_CHAT_ID)


@pytest.fixture
def dispatcher(_shared_dispatcher, monkeypatch):
    monkeypatch.setattr(feedback, "_cooldown", feedback.Cooldown())
    monkeypatch.setattr(feedback, "_waits", {})
    monkeypatch.setattr(albums, "ALBUM_SETTLE_SECONDS", 0.01)
    return _shared_dispatcher
