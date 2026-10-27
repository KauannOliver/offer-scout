import os

from celery import Celery
from celery.schedules import crontab
from celery.signals import beat_init, heartbeat_sent, worker_ready
from redis import Redis

from backend.app.config import get_settings

settings = get_settings()
celery_app = Celery(
    "money_extra",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["backend.app.tasks"],
)
celery_app.conf.update(
    beat_scheduler="backend.app.services.beat:ObservableScheduler",
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="America/Sao_Paulo",
    enable_utc=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_routes={
        "backend.app.tasks.discover_offers": {"queue": "browser"},
        "backend.app.tasks.check_browser_auth": {"queue": "browser"},
        "backend.app.tasks.prepare_selected_offers": {"queue": "browser"},
        "backend.app.tasks.publish_ready_offer": {"queue": "browser"},
        "backend.app.tasks.dispatch_publication": {"queue": "default"},
        "backend.app.tasks.telegram_private_smoke": {"queue": "default"},
        "backend.app.tasks.send_private_offer_preview": {"queue": "browser"},
        "backend.app.tasks.sync_coupons": {"queue": "browser"},
        "backend.app.tasks.publish_coupon": {"queue": "default"},
        "backend.app.tasks.dispatch_coupon_campaign": {"queue": "default"},
        "backend.app.tasks.operations_watchdog": {"queue": "default"},
        "backend.app.tasks.reprocess_settings": {"queue": "default"},
        "backend.app.tasks.reprocess_settings_batch": {"queue": "default"},
        "backend.app.tasks.dispatch_bot_promo": {"queue": "default"},
        "backend.app.tasks.dispatch_bad_offer": {"queue": "browser"},
        "backend.app.tasks.publish_channel_manifest": {"queue": "default"},
        "backend.app.tasks.dispatch_trust_reminder": {"queue": "default"},
        "backend.app.tasks.scheduler_heartbeat": {"queue": "default"},
    },
    beat_schedule={
        "editorial-calendar-every-minute": {
            "task": "backend.app.tasks.sync_editorial_calendar", "schedule": crontab(minute="*"),
        },
        "retention-daily": {
            "task": "backend.app.tasks.maintain_operational_data", "schedule": crontab(hour=3, minute=15),
        },
        "weekly-report-recovery": {
            "task": "backend.app.tasks.send_weekly_admin_report", "schedule": crontab(minute=10),
        },
        "settings-outbox-every-minute": {
            "task": "backend.app.tasks.process_settings_outbox",
            "schedule": crontab(minute="*"),
        },
        "scheduler-heartbeat-every-minute": {
            "task": "backend.app.tasks.scheduler_heartbeat",
            "schedule": crontab(minute="*"),
        },
        "check-browser-auth-every-five-minutes": {
            "task": "backend.app.tasks.check_browser_auth",
            "schedule": crontab(minute="*/5"),
        },
        "discover-every-ten-minutes": {
            "task": "backend.app.tasks.discover_offers",
            "schedule": crontab(minute="*"),
            "args": ["relevant"],
        },
        "discover-specialized-every-twenty-minutes": {
            "task": "backend.app.tasks.discover_offers",
            "schedule": crontab(minute="*"),
            "args": ["auto"],
        },
        "prepare-every-five-minutes": {
            "task": "backend.app.tasks.prepare_selected_offers",
            "schedule": crontab(minute="*/5"),
        },
        "sync-coupons-every-thirty-minutes": {
            "task": "backend.app.tasks.sync_coupons",
            "schedule": crontab(minute="*/30"),
        },
        "dispatch-publication-every-minute": {
            "task": "backend.app.tasks.dispatch_publication",
            "schedule": crontab(minute="*"),
        },
        "dispatch-coupon-campaign-every-ten-minutes": {
            "task": "backend.app.tasks.dispatch_coupon_campaign",
            "schedule": crontab(minute="5-59/10"),
        },
        "operations-watchdog-every-five-minutes": {
            "task": "backend.app.tasks.operations_watchdog",
            "schedule": crontab(minute="*/5"),
        },
        "bot-promo-slot-check": {
            "task": "backend.app.tasks.dispatch_bot_promo",
            "schedule": crontab(minute="*"),
        },
        "bad-offer-slot-check": {
            "task": "backend.app.tasks.dispatch_bad_offer",
            "schedule": crontab(minute="*"),
        },
        "weekly-trust-reminder-check": {
            "task": "backend.app.tasks.dispatch_trust_reminder",
            "schedule": crontab(minute="*"),
        },
    },
)


def _touch(key: str) -> None:
    client = Redis.from_url(settings.redis_url, decode_responses=True)
    try:
        client.set(key, "UP", ex=150)
    finally:
        client.close()


def _worker_hostname(sender) -> str:
    reported = getattr(sender, "hostname", None)
    if not reported or reported == "unknown":
        return os.environ.get("HOSTNAME", "unknown")
    return str(reported)


@worker_ready.connect
def worker_started(sender=None, **_kwargs) -> None:
    _touch(f"worker:role:{os.environ.get('WORKER_ROLE', 'general')}")


@heartbeat_sent.connect
def worker_heartbeat(sender=None, **_kwargs) -> None:
    _touch(f"worker:role:{os.environ.get('WORKER_ROLE', 'general')}")


@beat_init.connect
def scheduler_started(**_kwargs) -> None:
    _touch("scheduler:heartbeat")
