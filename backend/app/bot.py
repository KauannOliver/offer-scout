import asyncio
import contextlib
import hashlib
import json
import uuid
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from aiogram import BaseMiddleware, Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    BotCommand,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    Message,
)
from redis.asyncio import Redis
from sqlalchemy import Numeric, cast, desc, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from backend.app.config import get_settings
from backend.app.db import SessionLocal
from backend.app.models import (
    AppSetting,
    BotInteractionMetric,
    Coupon,
    CouponPublication,
    Product,
    Publication,
    PublicationKind,
    PublicationQueue,
)
from backend.app.services.bot_content import (
    coupon_summary,
    offer_summary,
    paginate_categories,
    public_post_url,
)
from backend.app.services.private_bot_state import RATE_SCRIPT, parse_search, published_offer
from backend.app.services.redis_lock import RELEASE_SCRIPT, RENEW_SCRIPT
from backend.app.services.telegram_retry import FloodControlRetry

settings = get_settings()
router = Router()
redis = Redis.from_url(settings.redis_url, decode_responses=True)


class PrivateCallbacksOnly(BaseMiddleware):
    async def __call__(self, handler, event, data):
        if not event.message or event.message.chat.type != "private" or event.message.chat.id != event.from_user.id:
            await event.answer("Abra o bot no privado para continuar.")
            return
        return await handler(event, data)


router.callback_query.outer_middleware(PrivateCallbacksOnly())


async def allowed(user_id: int) -> bool:
    async with SessionLocal() as session:
        stored = await session.scalar(select(AppSetting).where(AppSetting.key == "bot"))
    config = stored.value if stored else {}
    if not config.get("enabled", True):
        return False
    minute_limit = int(config.get("rate_per_minute", 10))
    day_limit = int(config.get("rate_per_day", 100))
    now = datetime.now(ZoneInfo("America/Sao_Paulo"))
    minute_key = f"bot:rate:minute:{user_id}:{now:%Y%m%d%H%M}"
    day_key = f"bot:rate:day:{user_id}:{now:%Y%m%d}"
    return bool(await redis.eval(RATE_SCRIPT, 2, minute_key, day_key, minute_limit, day_limit))


async def guard(message: Message) -> bool:
    if message.chat.type != "private" or not message.from_user:
        return False
    if not await allowed(message.from_user.id):
        await message.answer(
            "⏳ Muitas consultas em pouco tempo. Aguarde um minuto e tente novamente."
        )
        return False
    return True


@router.message(CommandStart())
async def start(message: Message) -> None:
    if not await guard(message):
        return
    await message.answer(
        "💸 <b>BEM-VINDO AO RADAR JULIUS</b>\n\n"
        "Eu organizo as ofertas que já foram publicadas no canal para você encontrar o que precisa sem perder tempo.\n\n"
        "Antes de uma oferta entrar no canal, o sistema registra histórico, calcula um score e revalida preço e disponibilidade. Cupons só aparecem quando estão confirmados.\n\n"
        "🔒 No privado eu não envio links comerciais: o botão leva à mensagem oficial do canal, onde ficam todos os detalhes. Você não paga nada a mais ao usar um link; o canal pode receber comissão.\n\n"
        "Escolha uma opção:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🔥 Últimas ofertas", callback_data="offers:all")],
                [InlineKeyboardButton(text="🏷️ Escolher categoria", callback_data="categories")],
                [InlineKeyboardButton(text="🎟️ Cupons ativos", callback_data="coupons")],
                [InlineKeyboardButton(text="🛡️ Como funciona", callback_data="about")],
            ]
        ),
    )


@router.message(Command("ajuda"))
async def help_command(message: Message) -> None:
    if await guard(message):
        await message.answer(
            "<b>Comandos disponíveis</b>\n"
            "/ofertas — últimas ofertas publicadas\n"
            "/categorias — escolher uma categoria\n"
            "/cupons — códigos ativos e compartilháveis\n"
            "/buscar — pesquisar nas ofertas já publicadas\n"
            "/favoritos — itens salvos por sete dias\n"
            "/como_funciona — conhecer o processo\n"
            "/criterios — entender os critérios do radar\n"
            "/canal — abrir o canal público\n"
            "/privacidade — consultar a política de privacidade\n"
            "/ajuda — mostrar esta ajuda"
        )


@router.message(Command("como_funciona"))
async def how_it_works(message: Message) -> None:
    if await guard(message):
        await message.answer(
            "🛡️ <b>COMO FUNCIONA</b>\n\n"
            "1. O radar pesquisa as interfaces oficiais do Mercado Livre.\n"
            "2. Preços e sinais comerciais entram no histórico.\n"
            "3. O score prioriza economia, confiança, popularidade e avaliação.\n"
            "4. Preço, disponibilidade e cupom são revalidados antes da publicação.\n\n"
            "Os links comerciais aparecem somente no canal público."
        )


@router.message(Command("criterios"))
async def criteria(message: Message) -> None:
    if await guard(message):
        await message.answer(
            "📊 <b>CRITÉRIOS DO RADAR</b>\n\n"
            "Comparamos desconto, histórico de preço, popularidade, avaliação, cupom confirmado, atualidade e confiança dos dados. Nem todo desconto anunciado vira recomendação.\n\n"
            "Quando há evidência suficiente de que o preço não compensa, publicamos uma análise financeira sem link para o produto reprovado."
        )


@router.message(Command("canal"))
async def channel_command(message: Message) -> None:
    if await guard(message):
        await message.answer(
            "📣 <b>ABRA O CANAL OFICIAL</b>", reply_markup=_channel_keyboard(_channel_url())
        )


@router.message(Command("privacidade"))
async def privacy(message: Message) -> None:
    if await guard(message):
        await message.answer(
            "🔒 <b>PRIVACIDADE</b>\n\n"
            "O atendimento é somente leitura. Não pedimos senha, documento ou dados de pagamento e não mantemos um perfil pessoal permanente. Contadores temporários no Redis são usados apenas para limitar abuso e expiram automaticamente."
        )


@router.message(Command("ofertas"))
async def offers_command(message: Message) -> None:
    if await guard(message):
        await track("OFFERS")
        await send_offers(message.bot, message.chat.id)


@router.message(Command("buscar"))
async def search_command(message: Message) -> None:
    if not await guard(message):
        return
    query = (message.text or "").partition(" ")[2].strip()
    if not query:
        await redis.set(f"bot:search_pending:{message.from_user.id}", "1", ex=300)
        await message.answer(
            "🔎 <b>O QUE VOCÊ ESTÁ PROCURANDO?</b>\n\n"
            "Digite o nome do produto. Exemplo: <code>cafeteira min:50 max:200</code>. Eu pesquiso apenas nas ofertas já publicadas no canal."
        )
        return
    await track("SEARCH")
    await send_offers(message.bot, message.chat.id, query=query)


@router.message(Command("favoritos"))
async def favorites_command(message: Message) -> None:
    if not await guard(message):
        return
    key = f"bot:favorites:v2:{message.from_user.id}"
    if (message.text or "").strip().endswith(" limpar"):
        await redis.delete(key)
        await message.answer("Favoritos removidos.")
        return
    await redis.zremrangebyscore(key, "-inf", datetime.now(UTC).timestamp())
    ids = await redis.zrange(key, 0, -1)
    await track("FAVORITES")
    await send_offers(message.bot, message.chat.id, publication_ids=list(ids))


@router.message(Command("categorias"))
async def categories_command(message: Message) -> None:
    if await guard(message):
        await track("CATEGORIES")
        await send_categories(message.bot, message.chat.id)


@router.message(Command("cupons"))
async def coupons_command(message: Message) -> None:
    if await guard(message):
        await track("COUPONS")
        await send_coupons(message.bot, message.chat.id)


@router.callback_query(F.data == "categories")
async def categories_callback(callback: CallbackQuery) -> None:
    if callback.from_user and await allowed(callback.from_user.id):
        await send_categories(callback.bot, callback.message.chat.id)
    await callback.answer()


@router.callback_query(F.data.startswith("categories:page:"))
async def categories_page_callback(callback: CallbackQuery) -> None:
    if not callback.from_user or not await allowed(callback.from_user.id):
        await callback.answer("Limite temporário atingido.", show_alert=True)
        return
    try:
        page = int((callback.data or "").rsplit(":", 1)[1])
    except ValueError:
        page = 0
    await send_categories(
        callback.bot,
        callback.message.chat.id,
        page=page,
        editable_message=callback.message,
    )
    await callback.answer()


@router.callback_query(F.data == "coupons")
async def coupons_callback(callback: CallbackQuery) -> None:
    if callback.from_user and await allowed(callback.from_user.id):
        await send_coupons(callback.bot, callback.message.chat.id)
    await callback.answer()


@router.callback_query(F.data == "about")
async def about_callback(callback: CallbackQuery) -> None:
    if callback.from_user and await allowed(callback.from_user.id):
        await callback.message.answer(
            "🛡️ O radar registra histórico, calcula score e revalida os fatos antes da publicação. Use /como_funciona ou /criterios para ver os detalhes."
        )
    await callback.answer()


@router.callback_query(F.data.startswith("offers:"))
async def offers_callback(callback: CallbackQuery) -> None:
    if not callback.from_user or not await allowed(callback.from_user.id):
        await callback.answer("Limite temporário atingido.", show_alert=True)
        return
    value = (callback.data or "").split(":", 1)[1]
    if value.startswith("page:"):
        try:
            _, token, raw_page = value.split(":", 2)
            requested_page = int(raw_page)
            if requested_page < 0 or requested_page > 10000 or len(token) != 12:
                raise ValueError
        except ValueError:
            await callback.answer("Navegação inválida. Use /ofertas novamente.")
            return
        stored = await redis.get(f"bot:offers:{token}")
        if not stored:
            await callback.answer("Esta navegação expirou. Use /ofertas novamente.", show_alert=True)
            return
        params = json.loads(stored)
        if params.get("owner") != callback.from_user.id:
            await callback.answer("Esta navegação pertence a outra sessão.")
            return
        await send_offers(
            callback.bot, callback.message.chat.id,
            category=params.get("category"), query=params.get("query"),
            page=requested_page, token=token, editable_message=callback.message,
            publication_ids=params.get("publication_ids"),
        )
    else:
        category = None if value == "all" else await redis.get(f"bot:category:{value}")
        if value != "all" and not category:
            await callback.answer("Categoria expirada. Abra /categorias novamente.", show_alert=True)
            return
        await track("CATEGORY", category or "")
        await send_offers(callback.bot, callback.message.chat.id, category)
    await callback.answer()


@router.callback_query(F.data.startswith("favorite:"))
async def favorite_callback(callback: CallbackQuery) -> None:
    if not callback.from_user or not await allowed(callback.from_user.id):
        await callback.answer("Limite temporário atingido.", show_alert=True)
        return
    publication_id = (callback.data or "").split(":", 1)[1]
    try:
        uuid.UUID(publication_id)
    except ValueError:
        await callback.answer("Oferta inválida.")
        return
    key = f"bot:favorites:v2:{callback.from_user.id}"
    async with SessionLocal() as session:
        valid = await session.scalar(select(Publication.id).where(
            Publication.id == uuid.UUID(publication_id), Publication.delivery_status == "SENT",
            Publication.public_message_url.is_not(None),
            Publication.publication_kind == PublicationKind.good_offer.value))
    if not valid:
        await callback.answer("Esta publicação não está disponível no canal.")
        return
    from backend.app.services.private_bot_state import FAVORITE_SCRIPT
    result = int(await redis.eval(FAVORITE_SCRIPT, 1, key, datetime.now(UTC).timestamp(), publication_id))
    if result == 1:
        await track("FAVORITE")
    await callback.answer({0: "Removido dos favoritos.", 1: "Salvo por sete dias.",
        -1: "Limite de 100 favoritos. Remova um ou use /favoritos limpar."}[result])


@router.callback_query(F.data.startswith("coupons:page:"))
async def coupons_page_callback(callback: CallbackQuery) -> None:
    if not callback.from_user or not await allowed(callback.from_user.id):
        await callback.answer("Limite temporário atingido.", show_alert=True)
        return
    try:
        page = int((callback.data or "0").rsplit(":", 1)[1])
    except ValueError:
        page = 0
    await send_coupons(callback.bot, callback.message.chat.id, page, callback.message)
    await callback.answer()


@router.message(F.text)
async def pending_search(message: Message) -> None:
    if not message.from_user or message.chat.type != "private":
        return
    pending_key = f"bot:search_pending:{message.from_user.id}"
    if not await redis.getdel(pending_key):
        return
    if not await guard(message):
        return
    query = (message.text or "").strip()
    if len(query) < 2:
        await message.answer("Digite pelo menos dois caracteres para pesquisar.")
        return
    await track("SEARCH")
    await send_offers(message.bot, message.chat.id, query=query)


async def send_categories(
    bot: Bot,
    chat_id: int,
    page: int = 0,
    editable_message: Message | None = None,
) -> None:
    async with SessionLocal() as session:
        published_rows = await session.execute(
            select(Product.category_name, func.count(func.distinct(Product.id)))
            .join(PublicationQueue, PublicationQueue.product_id == Product.id)
            .join(Publication, Publication.queue_id == PublicationQueue.id)
            .where(
                Product.category_name.is_not(None),
                Publication.published_at.is_not(None),
                Publication.delivery_status == "SENT",
                Publication.publication_kind == PublicationKind.good_offer.value,
            )
            .group_by(Product.category_name)
        )
        published = dict(published_rows.all())
    categories = sorted(
        ((category, int(count)) for category, count in published.items()),
        key=lambda item: item[0].casefold(),
    )
    current, safe_page, total_pages = paginate_categories(categories, page)
    rows: list[list[InlineKeyboardButton]] = []
    for category, count in current:
        digest = hashlib.sha256(category.encode()).hexdigest()[:12]
        await redis.set(f"bot:category:{digest}", category, ex=604800)
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{category[:36]} · {count} oferta{'s' if count != 1 else ''}",
                    callback_data=f"offers:{digest}",
                )
            ]
        )
    if not rows:
        await bot.send_message(chat_id, "Ainda não há categorias publicadas no canal.")
        return
    navigation: list[InlineKeyboardButton] = []
    if safe_page > 0:
        navigation.append(
            InlineKeyboardButton(text="⬅️ Anterior", callback_data=f"categories:page:{safe_page - 1}")
        )
    navigation.append(
        InlineKeyboardButton(text=f"{safe_page + 1}/{total_pages}", callback_data="categories:noop")
    )
    if safe_page + 1 < total_pages:
        navigation.append(
            InlineKeyboardButton(text="Próxima ➡️", callback_data=f"categories:page:{safe_page + 1}")
        )
    rows.append(navigation)
    markup = InlineKeyboardMarkup(inline_keyboard=rows)
    text = (
        f"🏷️ <b>ESCOLHA UMA CATEGORIA</b>\n"
        f"Página {safe_page + 1} de {total_pages} • {len(categories)} categorias com ofertas"
    )
    if editable_message:
        await editable_message.edit_text(text, reply_markup=markup)
    else:
        await bot.send_message(chat_id, text, reply_markup=markup)


@router.callback_query(F.data == "categories:noop")
async def categories_noop_callback(callback: CallbackQuery) -> None:
    await callback.answer("Indicador da página atual.")


async def send_offers(
    bot: Bot,
    chat_id: int,
    category: str | None = None,
    limit: int = 10,
    query: str | None = None,
    page: int = 0,
    token: str | None = None,
    editable_message: Message | None = None,
    publication_ids: list[str] | None = None,
) -> None:
    del limit  # Mantido na assinatura para compatibilidade com chamadas antigas.
    from sqlalchemy import tuple_
    state = json.loads(await redis.get(f"bot:offers:{token}") or "{}") if token else {}
    if token and state.get("owner") != chat_id:
        await _send_or_edit_text(bot, chat_id, "Esta navegação expirou. Use /ofertas novamente.", editable_message)
        return
    anchor = datetime.fromisoformat(state["anchor"]) if state.get("anchor") else datetime.now(UTC)
    cursors = state.get("cursors", {"0": None})
    page = max(0, page)
    if str(page) not in cursors:
        await _send_or_edit_text(bot, chat_id, "Página expirada. Reinicie com /ofertas ou /buscar.", editable_message)
        return
    async with SessionLocal() as session:
        statement = (
            select(Publication, PublicationQueue, Product)
            .join(PublicationQueue, Publication.queue_id == PublicationQueue.id)
            .join(Product, Product.id == PublicationQueue.product_id)
            .where(
                Publication.published_at.is_not(None),
                Publication.delivery_status == "SENT",
                Publication.publication_kind == PublicationKind.good_offer.value,
                Publication.public_message_url.is_not(None),
                Publication.published_at <= anchor,
            )
        )
        if category:
            statement = statement.where(Product.category_name == category)
        if query:
            words, minimum, maximum = parse_search(query[:160])
            for word in words:
                escaped = word.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                statement = statement.where(func.search_normalize(Product.title).like(func.search_normalize(f"%{escaped}%"), escape="\\"))
            price = cast(Publication.payload["facts"]["current_price"].as_string(), Numeric)
            if minimum is not None:
                statement = statement.where(price >= minimum)
            if maximum is not None:
                statement = statement.where(price <= maximum)
        if publication_ids is not None:
            favorite_key = f"bot:favorites:v2:{chat_id}"
            await redis.zremrangebyscore(favorite_key, "-inf", datetime.now(UTC).timestamp())
            current_favorites = set(await redis.zrange(favorite_key, 0, -1))
            valid_ids = []
            for raw_id in publication_ids:
                try:
                    if raw_id in current_favorites:
                        valid_ids.append(uuid.UUID(raw_id))
                except (TypeError, ValueError):
                    continue
            if not valid_ids:
                await _send_or_edit_text(
                    bot,
                    chat_id,
                    "⭐ Você ainda não salvou nenhuma oferta. Use o botão <b>Salvar</b> ao navegar.",
                    editable_message,
                )
                return
            statement = statement.where(Publication.id.in_(valid_ids))
        count_statement = select(func.count()).select_from(statement.subquery())
        total = int(await session.scalar(count_statement) or 0)
        safe_page = page
        boundary = cursors.get(str(page))
        if boundary:
            statement = statement.where(tuple_(Publication.published_at, Publication.id) < tuple_(
                datetime.fromisoformat(boundary[0]), uuid.UUID(boundary[1])))
        rows = (
            await session.execute(
                statement.order_by(desc(Publication.published_at), desc(Publication.id)).limit(2)
            )
        ).all()
    if not rows:
        message = "Nenhuma oferta publicada corresponde a essa busca."
        await _send_or_edit_text(bot, chat_id, message, editable_message)
        return
    publication, _, product = rows[0]
    title, price, image_url = published_offer(publication.payload or {})
    caption = offer_summary(title, price)
    published_at = publication.published_at.astimezone(ZoneInfo("America/Sao_Paulo"))
    caption += f"\n\nPublicado em {published_at:%d/%m/%Y às %H:%M}. Preço e disponibilidade podem mudar."
    caption += f"\n\n<b>{safe_page + 1} de {total}</b>"
    token = token or uuid.uuid4().hex[:12]
    cursors[str(safe_page + 1)] = [publication.published_at.isoformat(), str(publication.id)]
    state = {
        "owner": chat_id,
        "category": category,
        "query": query,
        "publication_ids": publication_ids,
        "anchor": anchor.isoformat(),
        "cursors": cursors,
    }
    await redis.set(f"bot:offers:{token}", json.dumps(state), ex=900)
    navigation: list[InlineKeyboardButton] = []
    if safe_page > 0:
        navigation.append(
            InlineKeyboardButton(
                text="⬅️", callback_data=f"offers:page:{token}:{safe_page - 1}"
            )
        )
    navigation.append(
        InlineKeyboardButton(text="⭐ Salvar / remover", callback_data=f"favorite:{publication.id}")
    )
    if len(rows) > 1:
        navigation.append(
            InlineKeyboardButton(
                text="➡️", callback_data=f"offers:page:{token}:{safe_page + 1}"
            )
        )
    keyboard_rows = [
        [
            InlineKeyboardButton(
                text="📣 VER PUBLICAÇÃO COMPLETA NO CANAL",
                url=publication.public_message_url,
            )
        ],
        navigation,
    ]
    markup = InlineKeyboardMarkup(inline_keyboard=keyboard_rows)
    await _send_or_edit_offer(
        bot, chat_id, image_url, caption, markup, editable_message
    )


async def send_coupons(
    bot: Bot,
    chat_id: int,
    page: int = 0,
    editable_message: Message | None = None,
) -> None:
    now = datetime.now(UTC)
    async with SessionLocal() as session:
        base = (
            select(Coupon, CouponPublication)
            .join(CouponPublication, CouponPublication.coupon_id == Coupon.id)
            .where(
                Coupon.shareable.is_(True),
                Coupon.lifecycle_status == "ACTIVE",
                CouponPublication.published_at.is_not(None),
                CouponPublication.rules_version == Coupon.rules_version,
                Coupon.expires_at.is_(None) | (Coupon.expires_at > now),
            )
        )
        total = int(await session.scalar(select(func.count()).select_from(base.subquery())) or 0)
        safe_page = min(max(page, 0), max(total - 1, 0))
        coupons = (
            await session.execute(
                base.order_by(desc(CouponPublication.published_at)).offset(safe_page).limit(1)
            )
        ).all()
    if not coupons:
        await _send_or_edit_text(
            bot,
            chat_id,
            "Nenhum cupom compartilhável está ativo neste momento.",
            editable_message,
        )
        return
    channel = await bot.get_chat(settings.telegram_public_channel_id)
    cards: list[tuple[str, str]] = []
    for coupon, publication in coupons:
        url = public_post_url(
            publication.destination, publication.telegram_message_id, channel.username
        )
        if not url:
            continue
        cards.append(
            (
                coupon_summary(
                    coupon.discount_percent,
                    coupon.discount_amount,
                    coupon.scope,
                    coupon.expires_at,
                ),
                url,
            )
        )
    if not cards:
        await _send_or_edit_text(
            bot,
            chat_id,
            "Os cupons estão ativos, mas as publicações do canal estão indisponíveis.",
            editable_message,
        )
        return
    summary, url = cards[0]
    summary += f"\n\n<b>{safe_page + 1} de {total}</b>"
    rows = [[InlineKeyboardButton(text="📣 VER CUPOM NO CANAL", url=url)]]
    navigation: list[InlineKeyboardButton] = []
    if safe_page > 0:
        navigation.append(
            InlineKeyboardButton(text="⬅️ Anterior", callback_data=f"coupons:page:{safe_page - 1}")
        )
    if safe_page + 1 < total:
        navigation.append(
            InlineKeyboardButton(text="Próximo ➡️", callback_data=f"coupons:page:{safe_page + 1}")
        )
    if navigation:
        rows.append(navigation)
    await _send_or_edit_text(
        bot, chat_id, summary, editable_message, InlineKeyboardMarkup(inline_keyboard=rows)
    )


async def track(action: str, category: str = "") -> None:
    day = datetime.now(ZoneInfo("America/Sao_Paulo")).date().isoformat()
    statement = pg_insert(BotInteractionMetric).values(
        day=day,
        action=action[:40],
        category=category[:120],
        count=1,
    )
    statement = statement.on_conflict_do_update(
        index_elements=["day", "action", "category"],
        set_={"count": BotInteractionMetric.count + 1},
    )
    async with SessionLocal() as session:
        await session.execute(statement)
        await session.commit()


async def _send_or_edit_text(
    bot: Bot,
    chat_id: int,
    text: str,
    editable_message: Message | None = None,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    if editable_message:
        try:
            await editable_message.edit_text(text, reply_markup=reply_markup)
            return
        except TelegramBadRequest as exc:
            if "message is not modified" in str(exc).lower():
                return
            if "message to edit not found" not in str(exc).lower():
                raise
    await bot.send_message(chat_id, text, reply_markup=reply_markup)


async def _send_or_edit_offer(
    bot: Bot,
    chat_id: int,
    image_url: str | None,
    caption: str,
    reply_markup: InlineKeyboardMarkup,
    editable_message: Message | None,
) -> None:
    if editable_message:
        try:
            if image_url:
                await editable_message.edit_media(
                    InputMediaPhoto(media=image_url, caption=caption),
                    reply_markup=reply_markup,
                )
            else:
                await editable_message.edit_text(caption, reply_markup=reply_markup)
            return
        except TelegramBadRequest as exc:
            if "message is not modified" in str(exc).lower():
                return
            if "message to edit not found" not in str(exc).lower():
                raise
    try:
        if image_url:
            await bot.send_photo(chat_id, image_url, caption=caption, reply_markup=reply_markup)
            return
    except TelegramBadRequest as exc:
        if not any(code in str(exc).lower() for code in ("wrong file", "failed to get http", "photo_invalid")):
            raise
    await bot.send_message(chat_id, caption, reply_markup=reply_markup)


def _channel_url() -> str:
    destination = settings.telegram_public_channel_id or ""
    return f"https://t.me/{destination[1:]}" if destination.startswith("@") else "https://t.me"


def _channel_keyboard(url: str | None) -> InlineKeyboardMarkup | None:
    if not url:
        return None
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📣 VER PUBLICAÇÃO COMPLETA NO CANAL", url=url)]
        ]
    )


async def main() -> None:
    if not settings.telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not configured")
    lock_token = str(uuid.uuid4())
    if not await redis.set("lock:telegram_private_bot", lock_token, ex=90, nx=True):
        raise RuntimeError("Another private bot poller is already active")
    bot = Bot(settings.telegram_bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    bot.session.middleware(FloodControlRetry())
    dispatcher = Dispatcher()
    dispatcher.include_router(router)
    heartbeat = asyncio.create_task(_heartbeat(lock_token, asyncio.current_task()))
    try:
        await bot.set_my_commands(
            [
                BotCommand(command="start", description="Abrir o atendimento"),
                BotCommand(command="ofertas", description="Ver as últimas ofertas"),
                BotCommand(command="buscar", description="Buscar ofertas publicadas"),
                BotCommand(command="favoritos", description="Ver itens salvos"),
                BotCommand(command="categorias", description="Escolher uma categoria"),
                BotCommand(command="cupons", description="Consultar cupons ativos"),
                BotCommand(command="como_funciona", description="Como o radar funciona"),
                BotCommand(command="criterios", description="Critérios das ofertas"),
                BotCommand(command="canal", description="Abrir o canal público"),
                BotCommand(command="privacidade", description="Privacidade do atendimento"),
                BotCommand(command="ajuda", description="Ver os comandos disponíveis"),
            ]
        )
        await bot.delete_webhook(drop_pending_updates=False)
        await dispatcher.start_polling(bot, allowed_updates=dispatcher.resolve_used_update_types())
    finally:
        heartbeat.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await heartbeat
        await redis.eval(RELEASE_SCRIPT, 1, "lock:telegram_private_bot", lock_token)
        await redis.aclose()


async def _heartbeat(lock_token: str, controller=None) -> None:
    while True:
        try:
            renewed = await redis.eval(RENEW_SCRIPT, 1, "lock:telegram_private_bot", lock_token, 90)
        except Exception:
            renewed = False
        if not renewed:
            if controller:
                controller.cancel()
            return
        await redis.set("bot:heartbeat", datetime.now(UTC).isoformat(), ex=90)
        await asyncio.sleep(30)


if __name__ == "__main__":
    asyncio.run(main())
