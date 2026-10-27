from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from redis.asyncio import Redis
from sqlalchemy import select, text

from backend.app.api.auth import router as auth_router
from backend.app.api.routes import router as dashboard_router
from backend.app.config import get_settings
from backend.app.db import SessionLocal
from backend.app.models import AdminUser, AppSetting
from backend.app.security import hash_password

settings = get_settings()
logger = structlog.get_logger()


@asynccontextmanager
async def lifespan(_: FastAPI):
    async with SessionLocal() as session:
        user = await session.scalar(
            select(AdminUser).where(AdminUser.username == settings.admin_username)
        )
        if not user:
            session.add(
                AdminUser(
                    username=settings.admin_username,
                    password_hash=hash_password(settings.admin_password),
                )
            )
        defaults = {
            "scoring": {
                "weights": {
                    "discount": 25,
                    "popularity": 18,
                    "rating": 14,
                    "commission": 12,
                    "coupon": 10,
                    "bonus": 8,
                    "price_confidence": 8,
                    "freshness": 5,
                },
                "minimum_score": 85,
                "minimum_discount": None,
                "minimum_rating": None,
                "minimum_sold_count": None,
                "minimum_commission": None,
                "minimum_price": None,
                "maximum_price": None,
            },
            "cadence": {"discovery_minutes": 10, "publish_minutes": 15, "daily_limit": 30},
            "deduplication": {
                "hours": 72,
                "price_drop_percent": 10,
                "coupon_improvement_points": 5,
            },
            "content": {
                "persona": "economic_parent",
                "impact": "balanced",
                "emoji_min": 4,
                "emoji_max": 7,
                "publish_without_coupon": True,
            },
            "discovery": {
                "max_items": 400,
                "specialized_max_items": 250,
                "max_scrolls": 20,
                "timeout_seconds": 45,
                "stable_rounds": 3,
                "priority_categories": [],
                "excluded_categories": [],
                "excluded_brands": [],
            },
            "editorial": {
                "max_same_category": 2,
                "max_same_brand": 1,
                "fact_freshness_minutes": 30,
                "quiet_hours": [],
            },
            "retention": {"snapshot_days": 180, "task_run_days": 90, "backup_days": 14},
            "campaigns": {
                "coupon_auto_publish": True,
                "coupon_daily_limit": 3,
                "coupon_spacing_minutes": 120,
            },
            "bot": {
                "enabled": True,
                "promo_enabled": True,
                "promo_times": ["10:30", "15:30", "20:30"],
                "promo_text": "",
                "rate_per_minute": 10,
                "rate_per_day": 100,
            },
            "bad_offers": {
                "enabled": True,
                "daily_limit": 2,
                "times": ["12:30", "19:30"],
                "maximum_score": 45,
                "min_observations": 3,
                "min_history_hours": 24,
                "freshness_minutes": 30,
                "minimum_confidence": 0.7,
                "min_reasons": 2,
                "repeat_days": 14,
                "alternative_min_score": 85,
                "retry_window_minutes": 90,
            },
            "channel_experience": {
                "manifest_enabled": True,
                "manifest_text": "",
                "weekly_enabled": True,
                "weekly_day": 7,
                "weekly_time": "18:00",
                "weekly_text": "",
                "global_spacing_minutes": 15,
            },
        }
        for key, value in defaults.items():
            exists = await session.scalar(select(AppSetting).where(AppSetting.key == key))
            if not exists:
                session.add(AppSetting(key=key, value=value))
        await session.commit()
    yield


app = FastAPI(title="Money Extra API", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(auth_router, prefix="/api/v1")
app.include_router(dashboard_router, prefix="/api/v1")


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.get("/ready")
async def ready() -> dict:
    dependencies = {
        "database": "DOWN", "redis": "DOWN", "workers": "DOWN",
        "scheduler": "DOWN", "private_bot": "DOWN",
    }
    async with SessionLocal() as session:
        await session.execute(text("SELECT 1"))
        dependencies["database"] = "UP"
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    try:
        if await redis.ping():
            dependencies["redis"] = "UP"
        workers = await redis.mget("worker:role:general", "worker:role:browser")
        dependencies["workers"] = "UP" if all(workers) else "DOWN"
        dependencies["scheduler"] = "UP" if await redis.get("scheduler:loop_heartbeat") else "DOWN"
        dependencies["private_bot"] = "UP" if await redis.get("bot:heartbeat") else "DOWN"
    finally:
        await redis.aclose()
    if "DOWN" in dependencies.values():
        raise HTTPException(status_code=503, detail=dependencies)
    return {"status": "ready", "dependencies": dependencies}
