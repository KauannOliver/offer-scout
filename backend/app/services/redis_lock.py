import asyncio
import contextlib
import secrets

from redis.asyncio import Redis

RELEASE_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
  return redis.call('del', KEYS[1])
else
  return 0
end
"""

RENEW_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
  return redis.call('expire', KEYS[1], ARGV[2])
else
  return 0
end
"""


class LockLostError(RuntimeError):
    """The controller must stop immediately after its lease is lost."""


class DistributedLock:
    def __init__(self, redis: Redis, key: str, ttl: int = 120, wait_timeout: int = 60):
        self.redis = redis
        self.key = key
        self.ttl = ttl
        self.wait_timeout = wait_timeout
        self.owner = secrets.token_urlsafe(24)
        self._renewal = None
        self._controller = None
        self._lost = False

    async def __aenter__(self) -> "DistributedLock":
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.wait_timeout
        while loop.time() < deadline:
            if await self.redis.set(self.key, self.owner, ex=self.ttl, nx=True):
                self._controller = asyncio.current_task()
                self._renewal = asyncio.create_task(self._keep_alive())
                return self
            await asyncio.sleep(0.25)
        raise TimeoutError(f"Could not acquire distributed lock: {self.key}")

    async def __aexit__(self, *_: object) -> None:
        if self._renewal:
            self._renewal.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._renewal
        with contextlib.suppress(Exception):
            await self.redis.eval(RELEASE_SCRIPT, 1, self.key, self.owner)
        if self._lost:
            raise LockLostError(f"Distributed lease lost: {self.key}")

    async def _keep_alive(self) -> None:
        while True:
            await asyncio.sleep(max(0.1, self.ttl / 3))
            try:
                renewed = await self.redis.eval(RENEW_SCRIPT, 1, self.key, self.owner, self.ttl)
            except Exception:
                renewed = False
            if not renewed:
                self._lost = True
                if self._controller:
                    self._controller.cancel()
                return
