import asyncio
import os
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import select, text

from backend.app.services.calendar import record_outcome, scheduled_slots
from backend.app.services.offer_policy import policy_fingerprint, repetition_allowed
from backend.app.services.private_bot_state import FAVORITE_SCRIPT
from backend.app.services.scoring import OfferScoringEngine, ScoreConfig

isolated = pytest.mark.skipif(os.getenv("RUN_ISOLATED_INTEGRATION") != "1", reason="Requires isolated DB and Redis")


@pytest.mark.parametrize("elapsed, expected", [(0, "PENDING"), (45, "PENDING"), (91, "EXPIRED")])
def test_slot_survives_missed_minute(elapsed, expected):
    now = datetime(2026, 9, 9, 13, 30, tzinfo=UTC)+timedelta(minutes=elapsed)
    specs = scheduled_slots({}, now)
    slot = next(s for s in specs if s["slot_key"] == "BOT_PROMO:2026-09-09:10:30")
    assert slot["state"] == expected


def test_slot_crosses_midnight_and_weekly_day():
    now = datetime(2026, 9, 7, 3, 15, tzinfo=UTC)  # Monday 00:15 SP
    specs = scheduled_slots({"bot": {"promo_times": ["23:45"]},
        "channel_experience": {"weekly_time": "23:45", "weekly_day": 7}}, now)
    assert any(s["kind"] == "TRUST_REMINDER" and s["state"] == "PENDING" and s["scheduled_for"] < now for s in specs)


@pytest.mark.parametrize("reason,state", [("delivery_uncertain", "RECONCILIATION"),
    ("interrupted", "RECONCILIATION"), ("global_spacing", "PENDING"), ("score_changed", "SKIPPED")])
def test_calendar_outcome(reason, state):
    now = datetime.now(UTC)
    slot = SimpleNamespace(state="RUNNING", deadline_at=now+timedelta(minutes=90))
    record_outcome(slot, {"reason": reason}, now)
    assert slot.state == state


def test_shared_repetition_policy_and_fingerprint():
    now = datetime.now(UTC)
    publication = SimpleNamespace(published_at=now, payload={"facts": {"current_price": "100"}, "effective_coupon_percent": "5"})
    assert not repetition_allowed(publication, Decimal(95), Decimal(9), {}, now)
    assert repetition_allowed(publication, Decimal(90), None, {}, now)
    assert repetition_allowed(publication, Decimal(100), Decimal(10), {}, now)
    assert repetition_allowed(publication, Decimal(100), None, {}, now+timedelta(hours=73))
    assert policy_fingerprint(OfferScoringEngine()) == policy_fingerprint(OfferScoringEngine())
    assert policy_fingerprint(OfferScoringEngine(ScoreConfig(minimum_score=10))) != policy_fingerprint(OfferScoringEngine())


def test_diversity_and_excluded_categories_are_real_filters():
    from backend.app.domain.types import NormalizedProduct
    from backend.app.services.editorial_policy import diversity_allowed
    engine = OfferScoringEngine(ScoreConfig(minimum_score=0, excluded_categories=["Eletronicos"], excluded_brands=["Marca X"]))
    item = NormalizedProduct(marketplace="mercado_livre", source="verification", item_id="test", title="Teste", product_url="https://www.mercadolivre.com.br/p/MLB123456",
        current_price=Decimal(100), raw_metadata={"category_name": "Eletrônicos", "brand": "marca x"})
    assert set(engine.score(item).reasons) == {"category_excluded", "brand_excluded"}
    assert not diversity_allowed(SimpleNamespace(category_name="Casa", brand=None), {}, [("Casa", None), ("Casa", None)])
    assert diversity_allowed(SimpleNamespace(category_name=None, brand=None), {}, [(None, None)])


@isolated
@pytest.mark.asyncio
async def test_restart_after_confirmed_send_reconciles_without_telegram():
    from backend.app.db import SessionLocal
    from backend.app.models import EditorialSlot
    from backend.app.services.calendar import reconcile_confirmed
    async with SessionLocal() as session:
        product, _ = await create_product(session)
        publication = await create_publication(session, product, datetime.now(UTC))
        publication.telegram_message_id = "verified-test-receipt"
        slot = EditorialSlot(slot_key=uuid.uuid4().hex, kind="GOOD_OFFER", state="RUNNING",
            scheduled_for=datetime.now(UTC), deadline_at=datetime.now(UTC)+timedelta(hours=1), next_attempt_at=datetime.now(UTC))
        session.add(slot)
        await session.commit()
        assert await reconcile_confirmed(session, "_publish_ready_offer", {"queue_id": str(publication.queue_id)}, slot)
        assert slot.state == "PUBLISHED"
        record_outcome(slot, {"reason": "unexpected_error"}, datetime.now(UTC))
        assert slot.state == "PUBLISHED"


@isolated
@pytest.mark.asyncio
async def test_favorites_atomic_limit_and_individual_expiry():
    from redis.asyncio import Redis

    from backend.app.config import get_settings
    client = Redis.from_url(get_settings().redis_url, decode_responses=True)
    key = f"verification:favorites:{uuid.uuid4()}"
    try:
        results = await asyncio.gather(*(client.eval(FAVORITE_SCRIPT, 1, key, 100, "same") for _ in range(2)))
        assert sorted(results) == [0, 1]
        assert await client.zcard(key) == 0
        assert await client.eval(FAVORITE_SCRIPT, 1, key, 100, "old") == 1
        assert await client.eval(FAVORITE_SCRIPT, 1, key, 200, "new") == 1
        await client.eval(FAVORITE_SCRIPT, 1, key, 604901, "third")
        assert set(await client.zrange(key, 0, -1)) == {"new", "third"}
        await client.zadd(key, {str(i): 9999999 for i in range(100)})
        assert await client.eval(FAVORITE_SCRIPT, 1, key, 605000, "overflow") == -1
    finally:
        await client.delete(key)
        await client.aclose()


async def create_product(session, title="Teste", old=False):
    from backend.app.models import Product, ProductCurrentState, ProductObservation, ProductSnapshot
    now = datetime.now(UTC)-timedelta(days=101 if old else 0)
    product = Product(title=title, source="verification", item_id=uuid.uuid4().hex,
        product_url="https://www.mercadolivre.com.br/p/MLB123456")
    session.add(product)
    await session.flush()
    snapshot = ProductSnapshot(product_id=product.id, price=Decimal(100), observed_at=now)
    session.add(snapshot)
    await session.flush()
    session.add(ProductCurrentState(product_id=product.id, snapshot_id=snapshot.id, last_verified_at=now))
    for i in range(3):
        session.add(ProductObservation(product_id=product.id, snapshot_id=snapshot.id, source="verification", observed_at=now+timedelta(minutes=i)))
    await session.flush()
    return product, snapshot


async def create_publication(session, product, timestamp, price="100"):
    from backend.app.models import Publication, PublicationQueue, QueueStatus
    queue = PublicationQueue(product_id=product.id, status=QueueStatus.published, idempotency_key=uuid.uuid4().hex)
    session.add(queue)
    await session.flush()
    publication = Publication(queue_id=queue.id, destination="verification", mode="TEST", payload_hash=uuid.uuid4().hex,
        published_at=timestamp, delivery_status="SENT", publication_kind="GOOD_OFFER",
        public_message_url=f"https://t.me/verification/{uuid.uuid4().int % 100000}",
        payload={"facts": {"title": product.title, "current_price": price}})
    session.add(publication)
    await session.flush()
    return publication


@isolated
@pytest.mark.asyncio
async def test_retention_atomic_rollup_idempotent_and_published_evidence():
    from backend.app.db import SessionLocal
    from backend.app.services.retention import retain_history
    async with SessionLocal() as session:
        assert await session.scalar(text("SELECT current_database()")) == "verification"
        unpublished, _ = await create_product(session, old=True)
        published, _ = await create_product(session, old=True)
        await create_publication(session, published, datetime.now(UTC))
        unpublished_id, published_id = unpublished.id, published.id
        await session.commit()
        preview = await retain_history(session)
        assert preview["observations_removed"] == 0
        await session.rollback()
        result = await retain_history(session, dry_run=False)
        assert result["observations_removed"] >= 3
        await session.commit()
        assert await session.scalar(text("SELECT count(*) FROM product_observations WHERE product_id=:id"), {"id": published_id}) == 3
        assert await session.scalar(text("SELECT sum(observation_count) FROM product_daily_history WHERE product_id=:id"), {"id": unpublished_id}) == 3
        again = await retain_history(session, dry_run=False)
        await session.commit()
        assert again["observations_removed"] == 0
        assert await session.scalar(text("SELECT sum(observation_count) FROM product_daily_history WHERE product_id=:id"), {"id": unpublished_id}) == 3


@isolated
@pytest.mark.asyncio
async def test_report_aggregate_no_commercial_links_or_user_ids():
    from backend.app.db import SessionLocal
    from backend.app.services.admin_report import aggregate_report, prepare_report, render_report
    async with SessionLocal() as session:
        payload = await aggregate_report(session)
        rendered = render_report(payload)
        assert "http" not in rendered and "meli.la" not in rendered
        assert "Comissão estimada" not in rendered
        key, first = await prepare_report(session, datetime(2080, 1, 8, tzinfo=UTC))
        await session.commit()
        _, second = await prepare_report(session, datetime(2080, 1, 8, tzinfo=UTC))
        assert second is None


@isolated
@pytest.mark.asyncio
async def test_calendar_restart_does_not_recreate_completed_slot():
    from backend.app.db import SessionLocal
    from backend.app.models import EditorialSlot
    from backend.app.services.calendar import slot_for_call, sync_calendar
    now = datetime(2081, 1, 9, 13, 45, tzinfo=UTC) + timedelta(days=uuid.uuid4().int % 100000)
    async with SessionLocal() as session:
        await sync_calendar(session, now)
        slot = await slot_for_call(session, "_dispatch_bot_promo", {}, now)
        assert slot and slot.scheduled_for < now
        slot.state = "PUBLISHED"
        key = slot.slot_key
        await session.commit()
        await sync_calendar(session, now)
        await session.commit()
        row = await session.scalar(select(EditorialSlot).where(EditorialSlot.slot_key == key))
        assert row.state == "PUBLISHED"
        assert await slot_for_call(session, "_dispatch_bot_promo", {}, now) is None


@isolated
@pytest.mark.asyncio
async def test_bot_cursor_stable_with_new_publications(monkeypatch):
    from backend.app import bot as module
    from backend.app.db import SessionLocal
    now = datetime.now(UTC)
    category = f"verification-{uuid.uuid4()}"
    async with SessionLocal() as session:
        product, _ = await create_product(session, "Preço publicado")
        product.category_name = category
        await create_publication(session, product, now-timedelta(minutes=1), "19.99")
        second = await create_publication(session, product, now-timedelta(minutes=2), "29.99")
        await session.commit()
    sent = []
    async def capture(*args): sent.append(args)
    monkeypatch.setattr(module, "_send_or_edit_offer", capture)
    owner = -int(uuid.uuid4().int % 10000000)
    await module.send_offers(None, owner, category=category)
    markup = sent[-1][4]
    callback = markup.inline_keyboard[1][-1].callback_data
    token = callback.split(":")[2]
    async with SessionLocal() as session:
        await create_publication(session, product, datetime.now(UTC), "999")
        await session.commit()
    await module.send_offers(None, owner, category=category, page=1, token=token)
    assert "29,99" in sent[-1][3]
    assert "999" not in sent[-1][3]
    assert sent[-1][4].inline_keyboard[0][0].url == second.public_message_url
    assert "mercadolivre" not in str(sent) and "meli.la" not in str(sent)
    await module.redis.delete(f"bot:offers:{token}")


@isolated
@pytest.mark.asyncio
async def test_settings_preview_and_reprocess_block_recent_publication():
    from backend.app.db import SessionLocal
    from backend.app.models import PublicationQueue, QueueStatus
    from backend.app.services.reprocessing import reprocess_after_settings
    async with SessionLocal() as session:
        product, _ = await create_product(session)
        await create_publication(session, product, datetime.now(UTC))
        await session.commit()
        result = await reprocess_after_settings(session, {"scoring": 8765}, product_ids={product.id},
            settings_values={"scoring": {"minimum_score": 0}, "deduplication": {"hours": 72}})
        assert result["newly_queued"] == 0
        rows = list(await session.scalars(select(PublicationQueue).where(PublicationQueue.product_id == product.id)))
        assert all(row.status == QueueStatus.published for row in rows)


@isolated
@pytest.mark.asyncio
async def test_manual_queue_actions_preserve_sent_and_active_replacement():
    from fastapi import HTTPException

    from backend.app.api.routes import discard_queue_item, queue_action
    from backend.app.db import SessionLocal
    from backend.app.models import PublicationQueue, QueueStatus
    from backend.app.services.queue_actions import can_mutate
    async with SessionLocal() as session:
        product, _ = await create_product(session)
        sent = await create_publication(session, product, datetime.now(UTC))
        queue_id, product_id = sent.queue_id, product.id
        await session.commit()
        for handler, args in ((discard_queue_item, {}), (queue_action, {"action": "retry"})):
            with pytest.raises(HTTPException) as error:
                await handler(queue_id, **args, _=None, session=session)
            assert error.value.status_code == 409
            await session.rollback()
        active = PublicationQueue(product_id=product_id, status=QueueStatus.ready, idempotency_key=str(uuid.uuid4()))
        stale = PublicationQueue(product_id=product_id, status=QueueStatus.skipped, idempotency_key=str(uuid.uuid4()))
        session.add_all([active, stale])
        await session.flush()
        assert not await can_mutate(session, stale, reactivate=True)
        active.status = QueueStatus.publishing
        assert not await can_mutate(session, active)
        await session.rollback()
