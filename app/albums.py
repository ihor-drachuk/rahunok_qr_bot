"""Album aggregation: the items of a Telegram media group that arrive together go to the handler in one call."""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import Message

# Telegram sends album items as separate updates and marks none of them as the last one,
# so we collect a group for this long after its first item arrives.
ALBUM_SETTLE_SECONDS = 1.0


class AlbumMiddleware(BaseMiddleware):
    """Collects the items of a media group that arrive within `ALBUM_SETTLE_SECONDS` of the first one and runs the
    handler once for them, with `album` holding them in message ID order. A later item starts a group of its own.
    A message outside a group gets `album = [message]`."""

    def __init__(self) -> None:
        self._groups: dict[tuple[int, str], list[Message]] = {}

    async def __call__(self, handler: Callable[[Message, dict[str, Any]], Awaitable[Any]], event: Message,
                       data: dict[str, Any]) -> Any:
        if event.media_group_id is None:
            data["album"] = [event]
            return await handler(event, data)
        key = (event.chat.id, event.media_group_id)
        if (group := self._groups.get(key)) is not None:
            group.append(event)  # the task that saw the group first handles all of it
            return None
        self._groups[key] = group = [event]
        await asyncio.sleep(ALBUM_SETTLE_SECONDS)
        del self._groups[key]
        group.sort(key=lambda message: message.message_id)
        data["album"] = group
        # `Command` filters read only the caption of the item they get, so we route the group by its item whose
        # caption is a command, if there is one.
        command = next((message for message in group if (message.caption or "").startswith("/")), group[0])
        return await handler(command, data)
