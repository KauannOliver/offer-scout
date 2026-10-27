import asyncio

import pytest

from backend.app.services.redis_lock import DistributedLock


class FakeRedis:
    def __init__(self):
        self.values: dict[str, str] = {}

    async def set(self, key, value, **_):
        if key in self.values:
            return False
        self.values[key] = value
        return True

    async def eval(self, _, __, key, owner):
        if self.values.get(key) != owner:
            return 0
        del self.values[key]
        return 1


@pytest.mark.asyncio
async def test_browser_lock_allows_only_one_controller():
    redis = FakeRedis()
    entered = asyncio.Event()
    release = asyncio.Event()

    async def holder():
        async with DistributedLock(redis, "lock:meli_browser", wait_timeout=1):
            entered.set()
            await release.wait()

    task = asyncio.create_task(holder())
    await entered.wait()
    with pytest.raises(TimeoutError):
        async with DistributedLock(redis, "lock:meli_browser", wait_timeout=0.01):
            pass
    release.set()
    await task
    assert "lock:meli_browser" not in redis.values
