export type Days = 1 | 7 | 30;
export type System = "azure_profile" | "b4";
type Status = "running" | "done" | "failed";

export type World = {
  persona_counts: Record<string, number>;
  day: number;
  first_live_day: number;
  thresholds_banner: string | null;
  spent_usd: number;
  limit_usd: number;
  periods: { period_id: number; first_day: number; last_day: number }[];
};

export type Flow = { from_client: string; to_client: string; count: number; eur: number };
export type GraphClient = { id: string; name: string; persona: string; blocked_from: number | null };
export type Network = {
  clients: GraphClient[];
  accounts: { id: string; client_id: string }[];
  arcs: Flow[];
};
export type Week = { week: number; payments: number; eur: number; alerts: number; blocks: number };
export type Client = {
  id: string;
  name: string;
  persona: string;
  accounts: string[];
  blocked_from: string | null;
  alerts: number;
};
export type ClientDetail = Omit<Client, "alerts"> & {
  occupation: string | null;
  description: string | null;
  purpose: string | null;
  expected_activity: string | null;
  declared_income: number;
  since: string | null;
  balance: number;
  spending: { week: number; category: string; eur: number }[];
  payments: { txn_id: string; ts: string; from_account: string; to_account: string; eur: number; category: string; reference: string | null }[];
  alerts: { alert_id: number; alert_date: string; account_id: string }[];
};
export type Estimate = { expected_alerts: number; cap: number; cost_usd: number; within_limit: boolean };
export type Job = {
  period_id: number;
  status: Status;
  day: string;
  days_total: number;
  days_completed?: number;
  investigations: number;
  cost_usd: number;
  error: string | null;
  alert_progress?: { number: number; total: number } | null;
};

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(path, options);
  if (response.ok) return response.json();
  const body = await response.json().catch(() => null);
  const detail = typeof body?.detail === "string" ? body.detail : null;
  throw new Error(detail ?? (response.status === 404 ? "This feature is unavailable." : "Request failed. Try again."));
}

function post<T>(path: string, payload: object) {
  return request<T>(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
}

export const api = {
  world: () => request<World>("/api/world"),
  graph: (day: number, signal: AbortSignal) => request<Network>(`/api/world/graph?day=${day}`, { signal }),
  timeline: () => request<Week[]>("/api/world/timeline"),
  clients: () => request<Client[]>("/api/clients"),
  client: (id: string, signal: AbortSignal) => request<ClientDetail>(`/api/clients/${encodeURIComponent(id)}`, { signal }),
  estimate: (days: Days, system: System, signal: AbortSignal) => request<Estimate>(`/api/estimate?days=${days}&system=${system}`, { signal }),
  simulate: (days: Days, system: System) => post<{ period_id: number }>("/api/simulate", { days, system }),
  create: (seed: number, persona_counts: Record<string, number>) => post<{ status: "created" }>("/api/world", { seed, persona_counts }),
  job: () => request<Job | null>("/api/jobs/current"),
  evaluator: () => request<{ account_id: string }[]>("/api/evaluator/groups"),
};

const money = (currency: string, digits: number) => new Intl.NumberFormat("en", {
  style: "currency", currency, maximumFractionDigits: digits, minimumFractionDigits: digits,
});
const dateFormat = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });
const shortDate = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", timeZone: "UTC" });
const firstDay = Date.UTC(2025, 2, 3);
export const dayDate = (day: number) => new Date(firstDay + (day - 1) * 86_400_000);
export const formatDate = (date: string | Date) => dateFormat.format(new Date(date));
export const formatWindow = (start: number, end: number) => shortDate.formatRange(dayDate(start), dayDate(end));
export const count = (value: number) => value.toLocaleString("en");
export const countLabel = (value: number, noun: string) => `${count(value)} ${noun}${value === 1 ? "" : "s"}`;
export const eur = money("EUR", 2).format;
export const compactEur = new Intl.NumberFormat("en", {
  style: "currency", currency: "EUR", notation: "compact", maximumFractionDigits: 1,
}).format;
export const usd = money("USD", 2).format;
export const label = (value: string) => value.replaceAll("_", " ").replace(/^./, (letter) => letter.toUpperCase());
export const errorText = (error: unknown) => error instanceof Error ? error.message : "Something went wrong.";
