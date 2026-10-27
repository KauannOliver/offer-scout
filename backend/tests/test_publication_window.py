from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from backend.app.api.routes import _next_publication_window


def test_past_cadence_never_returns_a_past_window():
    now = datetime(2026, 9, 2, 17, 10, 35, tzinfo=UTC)
    last = SimpleNamespace(published_at=now - timedelta(hours=2))
    value, reason = _next_publication_window(
        now=now,
        last_publication=last,
        publications_24h=[last],
        daily_limit=30,
        publish_minutes=15,
        ready_count=2,
        publishing_count=0,
        public_armed=True,
    )
    assert value == datetime(2026, 9, 2, 17, 11, tzinfo=UTC)
    assert reason == "SCHEDULED"


def test_no_ready_offer_has_no_misleading_timestamp():
    now = datetime.now(UTC)
    value, reason = _next_publication_window(
        now=now,
        last_publication=None,
        publications_24h=[],
        daily_limit=30,
        publish_minutes=15,
        ready_count=0,
        publishing_count=0,
        public_armed=True,
    )
    assert value is None
    assert reason == "WAITING_READY_OFFER"


def test_daily_limit_returns_first_rolling_release_slot():
    now = datetime(2026, 9, 2, 17, 10, tzinfo=UTC)
    publications = [SimpleNamespace(published_at=now - timedelta(hours=23, minutes=50))]
    value, reason = _next_publication_window(
        now=now,
        last_publication=publications[-1],
        publications_24h=publications,
        daily_limit=1,
        publish_minutes=15,
        ready_count=1,
        publishing_count=0,
        public_armed=True,
    )
    assert value == datetime(2026, 9, 2, 17, 21, tzinfo=UTC)
    assert reason == "DAILY_LIMIT"
