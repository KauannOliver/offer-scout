from pathlib import Path

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.types import FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup

from backend.app.config import get_settings
from backend.app.services.telegram_retry import FloodControlRetry


class TelegramService:
    def __init__(self):
        self.settings = get_settings()
        self.last_public_message_url: str | None = None

    def _bot(self) -> Bot:
        if not self.settings.telegram_bot_token:
            raise RuntimeError("TELEGRAM_BOT_TOKEN is not configured")
        bot = Bot(self.settings.telegram_bot_token)
        bot.session.middleware(FloodControlRetry())
        return bot

    async def validate_private_smoke(self) -> dict:
        if not self.settings.telegram_admin_chat_id:
            raise RuntimeError("TELEGRAM_ADMIN_CHAT_ID is not configured")
        async with self._bot() as bot:
            me = await bot.get_me()
            chat = await bot.get_chat(self.settings.telegram_admin_chat_id)
            if chat.type != "private":
                raise RuntimeError("The configured admin chat must be private")
            return {"bot_username": me.username, "chat_type": chat.type}

    async def send_private_smoke(self, image: Path | None = None) -> str:
        if not self.settings.telegram_admin_chat_id:
            raise RuntimeError("TELEGRAM_ADMIN_CHAT_ID is not configured")
        text = (
            "Money Extra conectado com sucesso.\n\n"
            "Este é um teste administrativo sem link de afiliado. "
            "As ofertas comerciais permanecem em dry-run."
        )
        async with self._bot() as bot:
            if image:
                result = await bot.send_photo(
                    self.settings.telegram_admin_chat_id, FSInputFile(image), caption=text
                )
            else:
                result = await bot.send_message(self.settings.telegram_admin_chat_id, text)
            return str(result.message_id)

    async def send_private_offer_preview(self, image: Path, caption: str) -> str:
        s = self.settings
        if s.telegram_delivery_mode != "private_smoke" or not s.telegram_admin_chat_id:
            raise RuntimeError("Private offer preview is not enabled")
        if "http://" in caption or "https://" in caption:
            raise ValueError("Private previews must not contain links")
        async with self._bot() as bot:
            chat = await bot.get_chat(s.telegram_admin_chat_id)
            if chat.type != "private":
                raise RuntimeError("Private preview destination must be a private chat")
            result = await bot.send_photo(chat.id, FSInputFile(image), caption=caption[:1024])
            return str(result.message_id)

    async def send_public_offer(self, image: Path, caption: str) -> str:
        s = self.settings
        if not (
            s.telegram_affiliate_publish_enabled
            and s.telegram_channel_declared
            and s.telegram_public_channel_id
            and s.telegram_delivery_mode == "public_auto"
        ):
            raise RuntimeError("Public affiliate publishing is not armed")
        async with self._bot() as bot:
            chat = await bot.get_chat(s.telegram_public_channel_id)
            if chat.type not in {"channel", "supergroup"} or not chat.username:
                raise RuntimeError("Destination must be a public channel or public supergroup")
            result = await bot.send_photo(
                chat.id, FSInputFile(image), caption=caption[:1024], parse_mode=ParseMode.HTML
            )
            self.last_public_message_url = f"https://t.me/{chat.username}/{result.message_id}"
            return str(result.message_id)

    async def public_message_url(self, message_id: str) -> str:
        async with self._bot() as bot:
            chat = await bot.get_chat(self.settings.telegram_public_channel_id)
            if not chat.username:
                raise RuntimeError("Public channel username is unavailable")
            return f"https://t.me/{chat.username}/{message_id}"

    async def send_public_bad_offer(
        self, image: Path, caption: str, alternative_url: str | None = None
    ) -> tuple[str, str]:
        s = self.settings
        if not (s.telegram_public_channel_id and s.telegram_channel_declared):
            raise RuntimeError("Public channel is not configured")
        keyboard = None
        if alternative_url:
            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="✅ VER ALTERNATIVA MELHOR AVALIADA",
                            url=alternative_url,
                        )
                    ]
                ]
            )
        async with self._bot() as bot:
            chat = await bot.get_chat(s.telegram_public_channel_id)
            if chat.type not in {"channel", "supergroup"} or not chat.username:
                raise RuntimeError("Destination must be public")
            result = await bot.send_photo(
                chat.id,
                FSInputFile(image),
                caption=caption[:1024],
                parse_mode=ParseMode.HTML,
                reply_markup=keyboard,
            )
            return str(result.message_id), f"https://t.me/{chat.username}/{result.message_id}"

    async def publish_manifest(
        self, text: str, existing_message_id: str | None = None
    ) -> tuple[str, str]:
        s = self.settings
        async with self._bot() as bot:
            chat = await bot.get_chat(s.telegram_public_channel_id)
            if chat.type not in {"channel", "supergroup"} or not chat.username:
                raise RuntimeError("Destination must be public")
            if existing_message_id:
                await bot.edit_message_text(
                    text=text[:4096],
                    chat_id=chat.id,
                    message_id=int(existing_message_id),
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=True,
                )
                message_id = existing_message_id
            else:
                message = await bot.send_message(
                    chat.id,
                    text=text[:4096],
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=True,
                )
                message_id = str(message.message_id)
            await bot.pin_chat_message(chat.id, int(message_id), disable_notification=True)
            return message_id, f"https://t.me/{chat.username}/{message_id}"

    async def send_public_trust_message(self, text: str) -> tuple[str, str]:
        s = self.settings
        async with self._bot() as bot:
            chat = await bot.get_chat(s.telegram_public_channel_id)
            if chat.type not in {"channel", "supergroup"} or not chat.username:
                raise RuntimeError("Destination must be public")
            message = await bot.send_message(
                chat.id,
                text=text[:4096],
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
            return str(message.message_id), f"https://t.me/{chat.username}/{message.message_id}"

    async def send_public_coupon(self, text: str) -> str:
        s = self.settings
        if not (
            s.telegram_affiliate_publish_enabled
            and s.telegram_channel_declared
            and s.telegram_public_channel_id
            and s.telegram_delivery_mode == "public_auto"
        ):
            raise RuntimeError("Public publishing is not armed")
        async with self._bot() as bot:
            chat = await bot.get_chat(s.telegram_public_channel_id)
            if chat.type not in {"channel", "supergroup"} or not chat.username:
                raise RuntimeError("Destination must be a public channel or public supergroup")
            result = await bot.send_message(chat.id, text=text[:4096], parse_mode=ParseMode.HTML)
            return str(result.message_id)

    async def send_public_bot_promo(self, text: str) -> tuple[str, str]:
        s = self.settings
        if not (s.telegram_public_channel_id and s.telegram_channel_declared):
            raise RuntimeError("Public channel is not configured")
        async with self._bot() as bot:
            me = await bot.get_me()
            if not me.username:
                raise RuntimeError("Bot username is unavailable")
            chat = await bot.get_chat(s.telegram_public_channel_id)
            if chat.type not in {"channel", "supergroup"} or not chat.username:
                raise RuntimeError("Destination must be public")
            message = await bot.send_message(
                chat.id,
                text=text.replace("{BOT_LINK}", f"https://t.me/{me.username}?start=canal")[:4096],
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
            return str(message.message_id), me.username

    async def bot_status(self) -> dict:
        async with self._bot() as bot:
            me = await bot.get_me()
            return {"username": me.username, "id": me.id, "can_join_groups": me.can_join_groups}

    async def validate_public_channel(self) -> dict:
        s = self.settings
        if not s.telegram_public_channel_id:
            raise RuntimeError("TELEGRAM_PUBLIC_CHANNEL_ID is not configured")
        async with self._bot() as bot:
            me = await bot.get_me()
            chat = await bot.get_chat(s.telegram_public_channel_id)
            if chat.type not in {"channel", "supergroup"} or not chat.username:
                raise RuntimeError("Destination must be public")
            member = await bot.get_chat_member(chat.id, me.id)
            can_post = member.status == "creator" or bool(
                getattr(member, "can_post_messages", False)
            )
            if not can_post:
                raise RuntimeError("Bot cannot post messages in the configured channel")
            return {
                "bot_username": me.username,
                "chat_type": chat.type,
                "channel_username": chat.username,
                "bot_status": member.status,
                "can_post_messages": can_post,
            }
