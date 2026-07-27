"""Custom-emoji IDs from the bot's emoji pack, resolved once at startup and read by texts.py.

Rendering a pack emoji inside message text uses the HTML <tg-emoji emoji-id="..."> tag (the bot
runs with ParseMode.HTML); the wrapped base emoji is the fallback shown when the custom one can't
render. Only the owner's Telegram Premium lets the bot send these — see resolve at startup.
"""

from aiogram import html

# base emoji -> custom_emoji_id; populated by app.stickers.resolve_custom_emoji() at startup.
_custom_emoji_ids: dict[str, str] = {}


def set_custom_emoji_ids(ids: dict[str, str]) -> None:
    _custom_emoji_ids.clear()
    _custom_emoji_ids.update(ids)


def render(base_emoji: str) -> str:
    emoji_id = _custom_emoji_ids.get(base_emoji)
    if emoji_id is None:
        return base_emoji
    return f'<tg-emoji emoji-id="{emoji_id}">{html.quote(base_emoji)}</tg-emoji>'
