import type { BadOfferRecord, BotStatus, CategoryCoverage, CouponProductPage, CouponRecord, CreativePreview, DashboardStatus, DuplicateReport, EditorialStatus, Opportunity, OpportunityPage, OperationsActivity, OperationsFunnel, OperationsHealth, Product, PublicationRecord, QueuePage, TaskRun, Trend } from "./types";

const API = "/api/v1";
import type { ProductOverview, OperationalCalendar } from "./types";

function csrfToken(): string {
  const token = document.cookie.split("; ").find((row) => row.startsWith("money_extra_csrf="));
  return token?.split("=")[1] ?? "";
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API}${path}`, {
    credentials: "include",
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.method && init.method !== "GET" ? { "X-CSRF-Token": csrfToken() } : {}),
      ...init?.headers,
    },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({ detail: response.statusText }));
    const detail = Array.isArray(body.detail)
      ? body.detail.map((item: { msg?: string; loc?: string[] }) => `${item.loc?.slice(1).join(" → ") ?? "Campo"}: ${item.msg ?? "valor inválido"}`).join("; ")
      : body.detail;
    throw new Error(typeof detail === "string" ? detail : "Falha na solicitação. Tente atualizar a página.");
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export const api = {
  login: (username: string, password: string) =>
    request<{ username: string; csrf_token: string }>("/auth/login", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    }),
  me: () => request<{ username: string }>("/auth/me"),
  logout: () => request<void>("/auth/logout", { method: "POST" }),
  status: () => request<DashboardStatus>("/status"),
  productOverview: (id: string, page = 1) => request<ProductOverview>(`/products/${id}/overview?page=${page}`),
  calendar: (query = "") => request<OperationalCalendar>(`/operations/calendar?${query}`),
  retentionPreview: () => request<{ observations_selected: number; days_selected: number; cutoff: string }>("/operations/retention"),
  weeklyReport: () => request<{ counts: Record<string, number>; start: string; end: string; limitations: string[]; categories: {category: string; count: number}[]; last_delivery: { delivery_status: string } | null }>("/operations/weekly-report"),
  products: () => request<Product[]>("/products"),
  opportunities: (page = 1) => request<Opportunity[]>(`/opportunities?page=${page}&page_size=30`),
  opportunityPage: (params: { page: number; search?: string; sort?: string; eligible?: boolean }) => {
    const query = new URLSearchParams({ page: String(params.page), page_size: "30", format: "page", sort: params.sort ?? "score" });
    if (params.search) query.set("search", params.search);
    if (params.eligible !== undefined) query.set("eligible", String(params.eligible));
    return request<OpportunityPage>(`/opportunities?${query}`);
  },
  queue: (params: { page?: number; status?: string; search?: string; sort?: string; category?: string; strategy?: string } = {}) => {
    const query = new URLSearchParams({ page: String(params.page ?? 1), page_size: "30", sort: params.sort ?? "priority" });
    if (params.status) query.set("status", params.status);
    if (params.search) query.set("search", params.search);
    if (params.category) query.set("category", params.category);
    if (params.strategy) query.set("strategy", params.strategy);
    return request<QueuePage>(`/queue?${query}`);
  },
  reorderQueue: (ids: string[]) => request<{ updated: number }>("/queue/reorder", { method: "PATCH", body: JSON.stringify({ ids }) }),
  queueOptions: () => request<{ categories: string[]; strategies: string[] }>("/queue/options"),
  bulkQueue: (ids: string[], action: string) => request<{ count: number }>("/queue/bulk", { method: "POST", body: JSON.stringify({ ids, action }) }),
  settings: () => request<Record<string, Record<string, unknown>>>("/settings"),
  settingsRevision: () => request<{ revision: number }>("/settings/revision"),
  updateSettingsBatch: (values: Record<string, Record<string, unknown>>, expected_revision?: number) =>
    request<{ changed: Record<string, number>; revision?: number; reprocess_task_id: string | null; detail: string }>("/settings/batch", {
      method: "PUT",
      body: JSON.stringify({ values, expected_revision }),
    }),
  settingsImpact: (values: Record<string, Record<string, unknown>>) =>
    request<{ products_evaluated: number; approved: number; estimated_entering: number; estimated_leaving: number; creative_regeneration: number; rescheduled: number }>("/settings/impact-preview", {
      method: "POST",
      body: JSON.stringify({ values }),
    }),
  reprocessing: (id: string) => request<{ id: string; state: string; result: unknown }>(`/operations/reprocessing/${id}`),
  updateSetting: (key: string, value: Record<string, unknown>) =>
    request<{ key: string; value: Record<string, unknown> }>(`/settings/${key}`, {
      method: "PUT",
      body: JSON.stringify({ value }),
    }),
  coupons: (params: { source?: string; status?: string; search?: string } = {}) => {
    const query = new URLSearchParams();
    if (params.source) query.set("source", params.source);
    if (params.status) query.set("status", params.status);
    if (params.search) query.set("search", params.search);
    return request<CouponRecord[]>(`/coupons?${query}`);
  },
  couponProducts: (id: string, page = 1) => request<CouponProductPage>(`/coupons/${id}/products?page=${page}&page_size=30`),
  publications: () => request<PublicationRecord[]>("/publications"),
  runs: () => request<TaskRun[]>("/runs"),
  discover: () => request<{ detail: string }>("/actions/discover", { method: "POST" }),
  runDiscovery: (strategy?: string) => request<{ detail: string }>("/discovery/run", { method: "POST", body: JSON.stringify({ strategy: strategy ?? null }) }),
  discoveryStrategies: () => request<{ default: string; strategies: string[]; categories: string[] }>("/discovery/strategies"),
  funnel: () => request<OperationsFunnel>("/operations/funnel"),
  operationsHealth: () => request<OperationsHealth>("/operations/health"),
  categoryCoverage: () => request<CategoryCoverage[]>("/operations/category-coverage"),
  trends: () => request<Trend[]>("/operations/trends"),
  telegramSmoke: () => request<{ detail: string }>("/actions/telegram-smoke", { method: "POST" }),
  syncCoupons: () => request<{ detail: string }>("/actions/sync-coupons", { method: "POST" }),
  revalidateCoupon: (id: string) => request<{ detail: string }>(`/coupons/${id}/revalidate`, { method: "POST" }),
  publishCoupon: (id: string) => request<{ detail: string }>(`/coupons/${id}/publish`, { method: "POST" }),
  validatePublicChannel: () => request<Record<string, unknown>>("/actions/validate-public-channel", { method: "POST" }),
  privateOfferPreview: () => request<{ detail: string }>("/actions/private-offer-preview", { method: "POST" }),
  botStatus: () => request<BotStatus>("/bot/status"),
  botPromo: () => request<{ detail: string }>("/bot/promo", { method: "POST" }),
  duplicateReport: () => request<DuplicateReport>("/operations/duplicates"),
  badOffers: () => request<BadOfferRecord[]>("/editorial/bad-offers"),
  badOfferStatus: () => request<EditorialStatus>("/editorial/bad-offers/status"),
  operationsActivity: () => request<OperationsActivity>("/operations/activity"),
  runBadOffer: () => request<{ detail: string }>("/editorial/bad-offers/run", { method: "POST" }),
  publishManifest: () => request<{ detail: string }>("/channel/onboarding/publish", { method: "POST" }),
  discard: (id: string) => request<{ status: string }>(`/queue/${id}/discard`, { method: "POST" }),
  queuePreview: (id: string) => request<CreativePreview>(`/queue/${id}/preview`),
  queueAction: (id: string, action: "regenerate" | "retry" | "publish-now" | "revalidate") =>
    request<{ status: string; task_id: string }>(`/queue/${id}/${action}`, { method: "POST" }),
};
