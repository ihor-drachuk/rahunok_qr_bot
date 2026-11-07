import asyncio

from app import albums
from app.albums import AlbumMiddleware
from tests.telegram_fakes import message_update


def album_item(message_id: int, caption: str | None = None):
    return message_update(message_id=message_id, photo=True, media_group_id="g", caption=caption).message


def test_item_after_settle_window_starts_own_group(monkeypatch):
    monkeypatch.setattr(albums, "ALBUM_SETTLE_SECONDS", 0.01)
    middleware = AlbumMiddleware()
    calls = []

    async def handler(event, data):
        calls.append((event.message_id, [item.message_id for item in data["album"]]))

    async def scenario():
        await asyncio.gather(middleware(handler, album_item(2), {}), middleware(handler, album_item(1), {}))
        await middleware(handler, album_item(3), {})

    asyncio.run(scenario())

    assert calls == [(1, [1, 2]), (3, [3])]


def test_group_is_routed_by_command_caption_wherever_it_is(monkeypatch):
    monkeypatch.setattr(albums, "ALBUM_SETTLE_SECONDS", 0.01)
    middleware = AlbumMiddleware()
    calls = []

    async def handler(event, data):
        calls.append(event.message_id)

    async def scenario():
        items = [album_item(1, "опис"), album_item(2), album_item(3, "/contact")]
        await asyncio.gather(*(middleware(handler, item, {}) for item in items))

    asyncio.run(scenario())

    assert calls == [3]


def test_message_outside_group_is_its_own_album():
    middleware = AlbumMiddleware()
    calls = []

    async def handler(event, data):
        calls.append(data["album"])

    message = message_update(message_id=5, text="реквізити").message
    asyncio.run(middleware(handler, message, {}))

    assert calls == [[message]]
