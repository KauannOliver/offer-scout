from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.app.services.telegram import TelegramService


@pytest.mark.asyncio
async def test_private_preview_rejects_any_link_before_sending():
    service = TelegramService()
    service.settings = SimpleNamespace(
        telegram_delivery_mode="private_smoke", telegram_admin_chat_id=1
    )
    with pytest.raises(ValueError, match="must not contain links"):
        await service.send_private_offer_preview(
            Path("unused.jpg"), "Oferta https://meli.la/example"
        )
