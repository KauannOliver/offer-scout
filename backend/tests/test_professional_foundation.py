import asyncio
import os
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select, text

from backend.app.services.editorial import evaluate_bad_offer
from backend.app.services.offer_policy import effective_coupon_percent
from backend.app.services.private_bot_state import parse_search, published_offer
from backend.app.services.redis_lock import DistributedLock, LockLostError


def test_published_facts_are_not_replaced_with_live_product():
    title, price, image = published_offer({"facts": {"title": "Oferta antiga", "current_price": "19.00"}, "image_source_url": "original"})
    assert (title, price, image) == ("Oferta antiga", Decimal("19"), "original")
    assert published_offer({})[1] is None


def test_search_explicit_price_bounds():
    assert parse_search("café elétrico min:20 max:99,90") == (["café", "elétrico"], Decimal(20), Decimal("99.90"))


@pytest.mark.asyncio
async def test_telegram_retries_only_definitive_rate_rejection(monkeypatch):
    from aiogram.exceptions import TelegramRetryAfter
    from aiogram.methods import GetMe

    from backend.app.services import telegram_retry
    from backend.app.services.telegram_retry import FloodControlRetry
    calls = []
    async def pause(seconds): calls.append("wait")
    monkeypatch.setattr(telegram_retry.asyncio, "sleep", pause)
    async def request(bot, method):
        calls.append("request")
        if len(calls) == 1:
            raise TelegramRetryAfter(method=method, message="rate", retry_after=1)
        return "ok"
    assert await FloodControlRetry()(request, None, GetMe()) == "ok"
    assert calls == ["request", "wait", "request"]
    calls.clear()
    async def uncertain(bot, method):
        calls.append("request")
        raise TimeoutError()
    with pytest.raises(TimeoutError):
        await FloodControlRetry()(uncertain, None, GetMe())
    assert calls == ["request"]


def test_nested_nan_setting_is_rejected():
    from pydantic import ValidationError

    from backend.app.schemas import SettingsBatchUpdate
    with pytest.raises(ValidationError):
        SettingsBatchUpdate(values={"scoring": {"weights": {"discount": float("nan")}}})


@pytest.mark.asyncio
async def test_relevant_strategy_does_not_click_ambiguous_mobile_control():
    from backend.app.services.browser import MercadoLivreBrowserService
    class Locator:
        def locator(self, selector):
            assert selector in {"#recommendations_card", "button.choice-selector__button"}
            return self
        def filter(self, **kwargs): return self
        async def count(self): return 1
        async def click(self, **kwargs): raise AssertionError("Already selected; no click expected")
    assert await MercadoLivreBrowserService(None)._apply_strategy(Locator(), "relevant")


def test_unknown_discount_is_not_financial_evidence():
    now = datetime.now(UTC)
    snapshots = [SimpleNamespace(price=Decimal(100), observed_at=now-timedelta(hours=h),
        discount_percent=None, metrics={"price_confidence": .9}) for h in (30, 25, 0)]
    result = evaluate_bad_offer(snapshots, Decimal(35))
    assert not result.eligible
    assert len(result.reasons) == 1


def test_coupon_cap_minimum_and_freshness():
    now = datetime.now(UTC)
    coupon = SimpleNamespace(shareable=True, code="TESTE", eligibility_confirmed=True,
        lifecycle_status="ACTIVE", starts_at=None, expires_at=now+timedelta(days=1),
        last_verified_at=now, remaining_budget=Decimal(500), minimum_purchase=Decimal(100),
        discount_percent=Decimal(20), discount_amount=None, maximum_discount=Decimal(10))
    assert effective_coupon_percent(coupon, Decimal(100), now) == 10
    assert effective_coupon_percent(coupon, Decimal(99), now) is None
    coupon.last_verified_at = now-timedelta(hours=1)
    assert effective_coupon_percent(coupon, Decimal(100), now) is None


@pytest.mark.asyncio
async def test_lock_renews_and_stops_on_loss():
    class Lease:
        calls = 0
        async def set(self, *args, **kwargs): return True
        async def eval(self, script, count, key, owner, *ttl):
            if ttl:
                self.calls += 1
                return int(self.calls < 2)
            return 0
    lease = Lease()
    with pytest.raises(LockLostError):
        async with DistributedLock(lease, "test", ttl=.3):
            await asyncio.sleep(2)
    assert lease.calls == 2


isolated = pytest.mark.skipif(os.getenv("RUN_ISOLATED_INTEGRATION") != "1", reason="Requires isolated Compose")


@isolated
@pytest.mark.asyncio
async def test_coupon_timeout_reservation_prevents_resend(monkeypatch):
    from backend.app import tasks
    from backend.app.db import SessionLocal
    from backend.app.models import Coupon, CouponPublication

    class UncertainTelegram:
        calls = 0
        async def send_public_coupon(self, caption):
            self.__class__.calls += 1
            raise TimeoutError("simulated response lost after send")

    monkeypatch.setattr(tasks, "_public_armed", lambda: True)
    monkeypatch.setattr(tasks, "TelegramService", UncertainTelegram)
    async with SessionLocal() as session:
        coupon = Coupon(marketplace_coupon_id=uuid.uuid4().hex, title="Cupom teste", code="TESTE",
            rules_version=uuid.uuid4().hex, shareable=True, lifecycle_status="ACTIVE",
            discount_percent=Decimal(10), last_verified_at=datetime.now(UTC))
        session.add(coupon)
        await session.commit()
        coupon_id = coupon.id
    first = await tasks._publish_coupon.__wrapped__(str(coupon_id))
    second = await tasks._publish_coupon.__wrapped__(str(coupon_id))
    assert first["reason"] == second["reason"] == "delivery_uncertain"
    assert UncertainTelegram.calls == 1
    async with SessionLocal() as session:
        intent = await session.scalar(select(CouponPublication).where(CouponPublication.coupon_id == coupon_id))
        assert intent.published_at is None
        assert intent.payload["delivery_status"] == "UNCERTAIN"


@isolated
@pytest.mark.asyncio
async def test_channel_lease_serializes_different_kinds(monkeypatch):
    from backend.app import tasks
    monkeypatch.setattr(tasks, "_public_armed", lambda: True)
    async def spacing(*args): return True
    monkeypatch.setattr(tasks, "_global_spacing_available", spacing)
    active = 0
    maximum = 0
    @tasks.serialized_channel_task
    async def publish(kind):
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        await asyncio.sleep(.1)
        active -= 1
        return {"kind": kind}
    await asyncio.gather(publish("COUPON"), publish("GOOD_OFFER"), publish("PROMO"))
    assert maximum == 1


@pytest.mark.asyncio
async def test_channel_dry_run_positional_never_requires_live_delivery(monkeypatch):
    from backend.app import tasks
    monkeypatch.setattr(tasks, "_public_armed", lambda: False)
    @tasks.serialized_channel_task
    async def publish(force=False, dry_run=False): return {"dry_run": dry_run}
    assert await publish(True, True) == {"dry_run": True}


@isolated
@pytest.mark.asyncio
async def test_settings_transaction_and_unchanged_observations():
    from backend.app.api.routes import update_settings_batch
    from backend.app.db import SessionLocal
    from backend.app.domain.types import NormalizedProduct
    from backend.app.models import (
        OfferScore,
        Product,
        ProductObservation,
        ProductSnapshot,
        PublicationQueue,
        ReprocessingJob,
        SettingsRevision,
    )
    from backend.app.schemas import SettingsBatchUpdate
    from backend.app.services.pipeline import OfferPipeline
    from backend.app.services.reprocessing import reprocess_after_settings
    async with SessionLocal() as session:
        assert await session.scalar(text("SELECT current_database()")) == "verification"
        initial_revision = (await session.get(SettingsRevision, 1)).revision
        payload = SettingsBatchUpdate(expected_revision=initial_revision, values={"cadence": {"discovery_minutes": 11 + initial_revision, "publish_minutes": 15}})
        result = await update_settings_batch(payload, None, session)
        assert result["revision"] == initial_revision+1
        assert await session.scalar(select(func.count()).select_from(ReprocessingJob)) >= 1
        with pytest.raises(HTTPException) as conflict:
            await update_settings_batch(payload, None, session)
        assert conflict.value.status_code == 409
        await session.rollback()
        unchanged = await update_settings_batch(SettingsBatchUpdate(values=payload.values), None, session)
        assert not unchanged["changed"]
        offer = NormalizedProduct(marketplace="mercado_livre", source="test", item_id=f"TEST{uuid.uuid4().hex}", title="Produto de teste", product_url="https://www.mercadolivre.com.br/p/MLB123456",
            current_price=Decimal(100), observed_at=datetime.now(UTC))
        pipeline = OfferPipeline()
        await pipeline.ingest(session, [offer], enqueue=False)
        await pipeline.ingest(session, [replace(offer, observed_at=datetime.now(UTC))], enqueue=False)
        product = await session.scalar(select(Product).where(Product.item_id == offer.item_id))
        assert await session.scalar(select(func.count()).select_from(ProductSnapshot).where(ProductSnapshot.product_id == product.id)) == 1
        assert await session.scalar(select(func.count()).select_from(ProductObservation).where(ProductObservation.product_id == product.id)) == 2
        assert await session.scalar(select(func.count()).select_from(OfferScore).where(OfferScore.product_id == product.id)) == 1
        from backend.app.services.scoring import OfferScoringEngine, ScoreConfig
        permissive = OfferPipeline(OfferScoringEngine(ScoreConfig(minimum_score=Decimal(0))))
        await permissive.ingest(session, [replace(offer, observed_at=datetime.now(UTC))], enqueue=True)
        assert await session.scalar(select(func.count()).select_from(PublicationQueue).where(PublicationQueue.product_id == product.id)) == 1
        assert permissive.metrics == {"new_products": 0, "updated_products": 0, "unchanged_products": 1}
        result = await reprocess_after_settings(session, {"scoring": 99}, product_ids=set())
        assert result["processed"] == 0
    from redis.asyncio import Redis

    from backend.app.config import get_settings
    from backend.app.services.settings_jobs import process_settings_outbox
    client = Redis.from_url(get_settings().redis_url)
    try:
        result = await process_settings_outbox(client)
        assert result["state"] == "SUCCEEDED"
    finally:
        await client.aclose()
