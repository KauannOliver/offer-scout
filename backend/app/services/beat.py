"""Heartbeat emitted by the actual scheduler loop, not by a worker task."""
from contextlib import suppress

from celery.beat import PersistentScheduler
from redis import Redis

from backend.app.config import get_settings


class ObservableScheduler(PersistentScheduler):
    def tick(self, *args, **kwargs):
        with suppress(Exception):
            with Redis.from_url(get_settings().redis_url) as client:
                client.set("scheduler:loop_heartbeat", "UP", ex=150)
        return min(super().tick(*args, **kwargs), 30)
