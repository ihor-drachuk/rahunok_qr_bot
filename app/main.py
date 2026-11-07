import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError
from aiogram.types import BotCommand

from app import emoji_pack, feedback, handlers, llm, texts
from app.albums import AlbumMiddleware
from app.config import load_bot_config

logger = logging.getLogger(__name__)


def build_dispatcher(stage_mode: bool, feedback_chat_id: int) -> Dispatcher:
    dispatcher = Dispatcher(stage_mode=stage_mode, feedback_chat_id=feedback_chat_id)
    dispatcher.message.outer_middleware(AlbumMiddleware())
    # `handlers.router` ends with catch-alls that take every message, so `feedback.router` goes first.
    dispatcher.include_router(feedback.router)
    dispatcher.include_router(handlers.router)
    return dispatcher


async def _check_feedback_chat(bot: Bot, feedback_chat_id: int) -> None:
    try:
        await bot.get_chat(feedback_chat_id)
    except TelegramAPIError as e:
        raise RuntimeError(
            f"Feedback chat {feedback_chat_id} is unreachable: {e.message}. Check RAHUNOK_QR_BOT_FEEDBACK_CHAT_ID, "
            "then press /start in the bot from that account, or add the bot to that group.") from e


async def _set_commands(bot: Bot) -> None:
    commands = [BotCommand(command="help", description=texts.COMMAND_HELP_DESCRIPTION),
                BotCommand(command="contact", description=texts.COMMAND_CONTACT_DESCRIPTION)]
    try:
        await bot.set_my_commands(commands)
    except TelegramAPIError as e:
        logger.warning("Failed to set the bot command menu: %s", e.message)


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    # httpx and aiogram log request URLs and update payloads (which carry user requisites) at DEBUG;
    # cap them so raising the root level to DEBUG cannot leak that content.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("aiogram.event").setLevel(logging.WARNING)
    cfg = load_bot_config()
    llm.init(cfg)
    async with Bot(cfg.telegram_bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML)) as bot:
        await _check_feedback_chat(bot, cfg.feedback_chat_id)
        dispatcher = build_dispatcher(cfg.stage_mode, cfg.feedback_chat_id)
        await emoji_pack.resolve_custom_emoji(bot)
        await _set_commands(bot)
        await bot.delete_webhook(drop_pending_updates=True)
        await dispatcher.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
