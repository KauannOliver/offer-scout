export type DashboardStatus = {
  environment: string;
  delivery_mode: string;
  affiliate_publish_enabled: boolean;
  channel_declared: boolean;
  groq_configured: boolean;
  telegram_configured: boolean;
  browser_state: string;
  queue_counts: Record<string, number>;
  product_count: number;
  eligible_count: number;
  published_24h: number;
  coupon_coverage: number;
  failed_count: number;
  daily_limit: number;
  next_publication_at: string | null;
  next_publication_reason: string;
  last_discovery_at: string | null;
  last_coupon_sync_at: string | null;
  last_publication_at: string | null;
  public_channel_configured: boolean;
};

export type Opportunity = {
  id: string; item_id: string; title: string; product_url: string; image_url: string | null;
  price: string | null; previous_price: string | null; discount_percent: string | null;
  rating: string | null; sold_text: string | null; score: string | null; classification: string | null;
  eligible: boolean; queue_id: string | null; queue_status: string | null;
  coupon: { id: string; code: string | null; discount_percent: string | null; status: string; reason: string | null } | null;
  category_name: string | null; brand: string | null; last_seen_at: string | null;
  discovery_count: number; price_metrics: { median_30d?: string | null; minimum_30d?: string | null; drop_from_last_percent?: string | null; price_confidence?: number };
};
export type OpportunityPage = { items: Opportunity[]; page: number; page_size: number; total: number };

export type OperationsFunnel = { cards_seen: number; unique_discovered: number; new_products: number; updated_products: number; unchanged_products: number; queue: Record<string, number>; published: number };
export type OperationsHealth = { database: string; redis: string; browser: string; crawler: string; groq: string; telegram: string; private_bot: string; workers: string; scheduler: string };
export type CategoryCoverage = { category: string; products: number; last_seen_at: string | null };
export type Trend = { product_id: string; title: string; category: string | null; price_drop_percent: string; price_confidence: number | null };

export type CouponRecord = {
  id: string; title: string; code: string | null; discount_percent: string | null;
  discount_amount: string | null; minimum_purchase: string | null; maximum_discount: string | null;
  expires_at: string | null; eligibility_confirmed: boolean; availability_text: string | null;
  restrictions: string[]; applicable_products: number; eligible_products_count: number;
  scope: string | null; remaining_budget: string | null; official_coupon_id: string | null;
  last_verified_at: string | null; rules_version: string | null;
  source_kind: string; shareable: boolean; lifecycle_status: string;
  activation_status: string; remaining_budget_value: string | null;
};

export type CouponProduct = {
  id: string; item_id: string; title: string; image_url: string | null; product_url: string;
  price: string | null; status: string; reason: string | null; checked_at: string | null;
};

export type CouponProductPage = { page: number; page_size: number; total: number; items: CouponProduct[] };

export type PublicationRecord = {
  id: string; queue_id: string; product_title: string; destination: string;
  telegram_message_id: string | null; mode: string; published_at: string | null;
  payload: { caption?: string; facts?: Record<string, unknown>; image_source_url?: string };
  publication_kind: "GOOD_OFFER" | "BAD_OFFER"; public_message_url: string | null;
  delivery_status: string; historical_duplicate: boolean;
};

export type TaskRun = {
  id: string; task_name: string; correlation_id: string; status: string;
  started_at: string; finished_at: string | null; duration_ms: number | null;
  result: Record<string, unknown>; error: string | null;
};

export type CreativePreview = { caption_html: string; image_url: string | null; generator: string; template_version: string };

export type Snapshot = {
  id: string;
  previous_price: string | null;
  price: string | null;
  discount_percent: string | null;
  rating: string | null;
  sold_text: string | null;
  sold_count: number | null;
  commission_percent: string | null;
  highlight: string | null;
  observed_at: string;
};

export type Product = {
  id: string;
  marketplace: string;
  source: string;
  item_id: string;
  product_id: string | null;
  title: string;
  product_url: string;
  image_url: string | null;
  active: boolean;
  snapshots: Snapshot[];
  updated_at: string;
};

export type QueueItem = {
  id: string;
  product_id: string;
  product_title: string;
  product_item_id: string;
  product_image_url: string | null;
  status: string;
  scheduled_for: string | null;
  attempts: number;
  last_error: string | null;
  operational_reason?: string | null;
  created_at: string;
  category_name: string | null;
  source_strategy: string | null;
  discovery_batch_id: string | null;
  priority: number;
  manual_position: number | null;
  publication_kind: "GOOD_OFFER" | "BAD_OFFER";
  editorial_payload: Record<string, unknown>;
};

export type QueuePage = { items: QueueItem[]; page: number; page_size: number; total: number; counts: Record<string, number> };

export type BotStatus = { state: string; username?: string; id?: number; config: Record<string, unknown>; error?: string };

export type DuplicateReport = {
  historical: { product_id: string; title: string; count: number }[];
  historical_products: number; historical_extra_posts: number;
  neutralized: { product_id: string; title: string; count: number }[];
  neutralized_queue_items: number; active_duplicate_groups: number;
};

export type BadOfferRecord = {
  id: string; product_id: string; product_title: string; image_url: string | null;
  status: string; created_at: string; published_at: string | null;
  public_message_url: string | null; delivery_status: string | null;
  payload: { caption?: string; evidence?: { reasons?: string[]; observation_count?: number; history_hours?: number; median_price?: string; current_price?: string }; alternative_url?: string | null };
};

export type EditorialStatus = {
  counts: Record<string, number>;
  next_attempt_at: string | null;
  slots: { id: string; slot_key: string; state: string; scheduled_for: string; next_attempt_at: string; outcome_reason: string | null; attempts: number }[];
};

export type OperationsActivity = {
  alerts: { kind: string; title: string; detail: string | null; at: string; entity_id: string }[];
  last_reprocessing: { id: string; status: string; result: Record<string, unknown>; at: string | null } | null;
};

export interface ProductOverview {
  id: string; title: string; image_url: string | null; category: string | null; brand: string | null;
  last_verified_at: string | null; observations: number;
  score: { total: string; eligible: boolean; reasons: string[]; components: Record<string, number | null> } | null;
  history: { total: number; items: { price: string | null; observed_at: string }[] };
  queue: { status: string; reason: string | null; error: string | null }[];
  coupons: { code: string | null; status: string; reason: string | null }[];
  publications: { url: string | null; published_at: string | null; delivery_status: string }[];
}
export interface OperationalCalendar {
  total: number; page: number; page_size: number;
  discoveries: { strategy: string; next_due_at: string; interval_minutes: number }[];
  slots: { id: string; kind: string; scheduled_for: string; next_attempt_at: string | null; state: string; reason: string | null }[];
}
