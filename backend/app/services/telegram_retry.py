"""Retry only a definitive flood-control rejection, never an uncertain send."""
import asyncio
import random

from aiogram.client.session.middlewares.base import BaseRequestMiddleware
from aiogram.exceptions import TelegramRetryAfter


class FloodControlRetry(BaseRequestMiddleware):
    async def __call__(self, make_request, bot, method):
        try:
            return await make_request(bot, method)
        except TelegramRetryAfter as error:
            if error.retry_after > 30:
                raise
            await asyncio.sleep(error.retry_after + random.uniform(.1, .4))
            return await make_request(bot, method)
