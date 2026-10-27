import enum
import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class UUIDTimestampMixin:
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class BrowserAuthState(enum.StrEnum):
    auth_required = "AUTH_REQUIRED"
    authenticated = "AUTHENTICATED"
    challenge_required = "AUTH_CHALLENGE_REQUIRED"
    session_expired = "SESSION_EXPIRED"


class QueueStatus(enum.StrEnum):
    discovered = "DISCOVERED"
    selected = "SELECTED"
    link_pending = "LINK_PENDING"
    creative_pending = "CREATIVE_PENDING"
    ready = "READY"
    publishing = "PUBLISHING"
    dry_run = "DRY_RUN"
    published = "PUBLISHED"
    retry = "RETRY"
    skipped = "SKIPPED"
    failed = "FAILED"


class PublicationKind(enum.StrEnum):
    good_offer = "GOOD_OFFER"
    bad_offer = "BAD_OFFER"


class CouponApplicabilityStatus(enum.StrEnum):
    pending = "PENDING"
    applicable = "APPLICABLE"
    not_applicable = "NOT_APPLICABLE"
    expired = "EXPIRED"


class AdminUser(Base, UUIDTimestampMixin):
    __tablename__ = "admin_users"
    username: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Product(Base, UUIDTimestampMixin):
    __tablename__ = "products"
    __table_args__ = (UniqueConstraint("marketplace", "item_id"),)
    marketplace: Mapped[str] = mapped_column(String(40), default="mercado_livre")
    source: Mapped[str] = mapped_column(String(80), default="affiliate_hub")
    item_id: Mapped[str] = mapped_column(String(64), index=True)
    product_id: Mapped[str | None] = mapped_column(String(64), index=True)
    user_product_id: Mapped[str | None] = mapped_column(String(64))
    variation_id: Mapped[str | None] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(Text)
    product_url: Mapped[str] = mapped_column(Text)
    image_url: Mapped[str | None] = mapped_column(Text)
    category_name: Mapped[str | None] = mapped_column(String(160), index=True)
    brand: Mapped[str | None] = mapped_column(String(160), index=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    discovery_count: Mapped[int] = mapped_column(Integer, default=0)
    discovery_strategies: Mapped[list] = mapped_column(JSON, default=list)
    raw_metadata: Mapped[dict] = mapped_column(JSON, default=dict)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    snapshots: Mapped[list["ProductSnapshot"]] = relationship(
        cascade="all, delete-orphan", back_populates="product"
    )


class ProductSnapshot(Base, UUIDTimestampMixin):
    __tablename__ = "product_snapshots"
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id"), index=True)
    previous_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    discount_percent: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    rating: Mapped[Decimal | None] = mapped_column(Numeric(3, 2))
    sold_text: Mapped[str | None] = mapped_column(String(120))
    sold_count: Mapped[int | None] = mapped_column(Integer)
    commission_percent: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    extra_commission: Mapped[bool] = mapped_column(Boolean, default=False)
    brand_commission: Mapped[bool] = mapped_column(Boolean, default=False)
    highlight: Mapped[str | None] = mapped_column(String(120))
    currency: Mapped[str] = mapped_column(String(8), default="BRL")
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    product: Mapped[Product] = relationship(back_populates="snapshots")


class ProductObservation(Base, UUIDTimestampMixin):
    __tablename__ = "product_observations"
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id"), index=True)
    snapshot_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("product_snapshots.id"))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    source: Mapped[str] = mapped_column(String(180))


class ProductCurrentState(Base):
    __tablename__ = "product_current_state"
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id"), primary_key=True)
    snapshot_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("product_snapshots.id"))
    score_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("offer_scores.id"))
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    score_total: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    score_eligible: Mapped[bool | None] = mapped_column(Boolean)
    price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    discount_percent: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))


class SettingsRevision(Base):
    __tablename__ = "settings_revision"
    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    revision: Mapped[int] = mapped_column(Integer, default=0)


class DiscoverySchedule(Base):
    __tablename__ = "discovery_schedule"
    strategy: Mapped[str] = mapped_column(String(40), primary_key=True)
    next_due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    interval_minutes: Mapped[int] = mapped_column(Integer)


class ReprocessingJob(Base, UUIDTimestampMixin):
    __tablename__ = "reprocessing_jobs"
    revision: Mapped[int] = mapped_column(Integer, unique=True)
    changes: Mapped[dict] = mapped_column(JSON, default=dict)
    settings_values: Mapped[dict] = mapped_column(JSON, default=dict)
    state: Mapped[str] = mapped_column(String(30), default="PENDING", index=True)
    cursor: Mapped[uuid.UUID | None] = mapped_column()
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(String(255))


class Coupon(Base, UUIDTimestampMixin):
    __tablename__ = "coupons"
    marketplace_coupon_id: Mapped[str] = mapped_column(String(100), unique=True)
    title: Mapped[str] = mapped_column(Text)
    code: Mapped[str | None] = mapped_column(String(100))
    discount_percent: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    discount_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    minimum_purchase: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    maximum_discount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    availability_text: Mapped[str | None] = mapped_column(Text)
    restrictions: Mapped[list] = mapped_column(JSON, default=list)
    eligibility_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    source_fingerprint: Mapped[str | None] = mapped_column(String(64), index=True)
    rules_version: Mapped[str | None] = mapped_column(String(64), index=True)
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_kind: Mapped[str] = mapped_column(String(40), default="affiliate_generated", index=True)
    shareable: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    lifecycle_status: Mapped[str] = mapped_column(String(30), default="ACTIVE", index=True)
    activation_status: Mapped[str] = mapped_column(String(30), default="GENERATED")
    scope: Mapped[str | None] = mapped_column(String(240))
    remaining_budget: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    raw_metadata: Mapped[dict] = mapped_column(JSON, default=dict)


class CouponProductApplicability(Base, UUIDTimestampMixin):
    __tablename__ = "coupon_product_applicability"
    __table_args__ = (UniqueConstraint("coupon_id", "product_id"),)
    coupon_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("coupons.id"), index=True)
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id"), index=True)
    status: Mapped[CouponApplicabilityStatus] = mapped_column(
        Enum(CouponApplicabilityStatus), default=CouponApplicabilityStatus.pending
    )
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)
    reason: Mapped[str | None] = mapped_column(Text)
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OfferScore(Base, UUIDTimestampMixin):
    __tablename__ = "offer_scores"
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id"), index=True)
    snapshot_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("product_snapshots.id"))
    total: Mapped[Decimal] = mapped_column(Numeric(6, 2))
    classification: Mapped[str] = mapped_column(String(40))
    components: Mapped[dict] = mapped_column(JSON)
    eligible: Mapped[bool] = mapped_column(Boolean, default=False)
    reasons: Mapped[list] = mapped_column(JSON, default=list)
    policy_fingerprint: Mapped[str | None] = mapped_column(String(64))


class AffiliateLink(Base, UUIDTimestampMixin):
    __tablename__ = "affiliate_links"
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id"), index=True)
    full_url: Mapped[str | None] = mapped_column(Text)
    short_url: Mapped[str] = mapped_column(Text)
    affiliate_tag: Mapped[str] = mapped_column(String(100))
    source: Mapped[str] = mapped_column(String(40), default="link_builder")
    validated_format: Mapped[bool] = mapped_column(Boolean, default=False)
    generation_metadata: Mapped[dict] = mapped_column(JSON, default=dict)


class Creative(Base, UUIDTimestampMixin):
    __tablename__ = "creatives"
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id"), index=True)
    copy_text: Mapped[str] = mapped_column(Text)
    image_path: Mapped[str | None] = mapped_column(Text)
    image_source_url: Mapped[str | None] = mapped_column(Text)
    template_version: Mapped[str] = mapped_column(String(40), default="telegram-v2")
    content_setting_version: Mapped[int | None] = mapped_column(Integer)
    generator: Mapped[str] = mapped_column(String(40))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)


class PublicationQueue(Base, UUIDTimestampMixin):
    __tablename__ = "publication_queue"
    __table_args__ = (UniqueConstraint("idempotency_key"),)
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id"), index=True)
    score_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("offer_scores.id"))
    link_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("affiliate_links.id"))
    creative_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("creatives.id"))
    status: Mapped[QueueStatus] = mapped_column(Enum(QueueStatus), default=QueueStatus.discovered)
    idempotency_key: Mapped[str] = mapped_column(String(255))
    scheduled_for: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
    operational_reason: Mapped[str | None] = mapped_column(Text)
    discovery_batch_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    source_strategy: Mapped[str | None] = mapped_column(String(180), index=True)
    priority: Mapped[int] = mapped_column(Integer, default=0, index=True)
    manual_position: Mapped[int | None] = mapped_column(Integer, index=True)
    publication_kind: Mapped[str] = mapped_column(
        String(30), default=PublicationKind.good_offer.value, index=True
    )
    editorial_payload: Mapped[dict] = mapped_column(JSON, default=dict)


class Publication(Base, UUIDTimestampMixin):
    __tablename__ = "publications"
    queue_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("publication_queue.id"), unique=True)
    destination: Mapped[str] = mapped_column(String(255))
    telegram_message_id: Mapped[str | None] = mapped_column(String(80))
    mode: Mapped[str] = mapped_column(String(40))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    payload_hash: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    publication_kind: Mapped[str] = mapped_column(
        String(30), default=PublicationKind.good_offer.value, index=True
    )
    public_message_url: Mapped[str | None] = mapped_column(Text)
    delivery_status: Mapped[str] = mapped_column(String(30), default="SENT", index=True)
    dedupe_key: Mapped[str | None] = mapped_column(String(255), unique=True)
    historical_duplicate: Mapped[bool] = mapped_column(Boolean, default=False, index=True)


class CouponPublication(Base, UUIDTimestampMixin):
    __tablename__ = "coupon_publications"
    __table_args__ = (UniqueConstraint("coupon_id", "rules_version"),)
    coupon_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("coupons.id"), index=True)
    rules_version: Mapped[str] = mapped_column(String(64))
    destination: Mapped[str] = mapped_column(String(255))
    telegram_message_id: Mapped[str | None] = mapped_column(String(80))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)


class TaskRun(Base, UUIDTimestampMixin):
    __tablename__ = "task_runs"
    task_name: Mapped[str] = mapped_column(String(120), index=True)
    correlation_id: Mapped[str] = mapped_column(String(80), index=True)
    status: Mapped[str] = mapped_column(String(30), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text)


class EditorialCandidate(Base, UUIDTimestampMixin):
    __tablename__ = "editorial_candidates"
    __table_args__ = (UniqueConstraint("product_id"),)
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id"), index=True)
    latest_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("product_snapshots.id")
    )
    state: Mapped[str] = mapped_column(String(40), default="WATCHING", index=True)
    score: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    price_confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    observation_count: Mapped[int] = mapped_column(Integer, default=0)
    history_hours: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    reasons: Mapped[list] = mapped_column(JSON, default=list)
    last_evaluated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    next_revalidation_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


class EditorialSlot(Base, UUIDTimestampMixin):
    __tablename__ = "editorial_slots"
    slot_key: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    kind: Mapped[str] = mapped_column(String(40), index=True)
    scheduled_for: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    deadline_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    state: Mapped[str] = mapped_column(String(40), default="PENDING", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    outcome_reason: Mapped[str | None] = mapped_column(String(120))
    candidate_count: Mapped[int] = mapped_column(Integer, default=0)
    product_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("products.id"))
    publication_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("publications.id"))
    policy_version: Mapped[int] = mapped_column(Integer, default=1)


class BotInteractionMetric(Base, UUIDTimestampMixin):
    __tablename__ = "bot_interaction_metrics"
    __table_args__ = (UniqueConstraint("day", "action", "category"),)
    day: Mapped[str] = mapped_column(String(10), index=True)
    action: Mapped[str] = mapped_column(String(40), index=True)
    category: Mapped[str] = mapped_column(String(160), default="")
    count: Mapped[int] = mapped_column(Integer, default=0)


class AppSetting(Base, UUIDTimestampMixin):
    __tablename__ = "app_settings"
    key: Mapped[str] = mapped_column(String(100), unique=True)
    value: Mapped[dict] = mapped_column(JSON)
    description: Mapped[str | None] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, default=1)


class BotCampaignMessage(Base, UUIDTimestampMixin):
    __tablename__ = "bot_campaign_messages"
    slot_key: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    destination: Mapped[str] = mapped_column(String(255))
    telegram_message_id: Mapped[str | None] = mapped_column(String(80))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)


class ChannelEditorialMessage(Base, UUIDTimestampMixin):
    __tablename__ = "channel_editorial_messages"
    message_key: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    kind: Mapped[str] = mapped_column(String(40), index=True)
    version: Mapped[str] = mapped_column(String(64))
    destination: Mapped[str] = mapped_column(String(255))
    telegram_message_id: Mapped[str | None] = mapped_column(String(80))
    public_message_url: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)


class BrowserSession(Base, UUIDTimestampMixin):
    __tablename__ = "browser_sessions"
    state: Mapped[BrowserAuthState] = mapped_column(
        Enum(BrowserAuthState), default=BrowserAuthState.auth_required
    )
    last_authenticated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    message: Mapped[str | None] = mapped_column(Text)


class AuditEvent(Base, UUIDTimestampMixin):
    __tablename__ = "audit_events"
    actor: Mapped[str] = mapped_column(String(100), default="system")
    action: Mapped[str] = mapped_column(String(100), index=True)
    entity_type: Mapped[str | None] = mapped_column(String(80))
    entity_id: Mapped[str | None] = mapped_column(String(100))
    details: Mapped[dict] = mapped_column(JSON, default=dict)
