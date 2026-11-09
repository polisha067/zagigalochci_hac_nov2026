export interface User {
  id: string;
  name: string;
  email: string;
}
export interface Verification {
  status: "verification_required";
  verification_id: string;
  email: string;
  expires_in: number;
  resend_after: number;
}
export type AuthResponse = { user: User } | Verification;
export interface Options {
  dataset_id: string;
  data_status: string;
  cities: string[];
  cargo_types: { id: string; name: string }[];
  limits: {
    shipments: number;
    route_points: number;
    cargoes: number;
    horizon_days: number;
  };
}
export interface CargoItem {
  id: string;
  cargo_type: string;
  weight_tonnes: number;
  volume_m3?: number | null;
  load_at: number;
  unload_at: number | null;
  splittable: boolean;
}
export type Schedule =
  | { mode: "once"; ready_at: string }
  | {
      mode: "regular";
      start_at: string;
      end_at: string;
      frequency: {
        count: number;
        every: number;
        unit: "day" | "week" | "month";
      };
    }
  | { mode: "explicit"; ready_at: string[] };
export interface CalculationRequest {
  dataset_id: string;
  route: { city: string; service_hours: number }[];
  cargoes: CargoItem[];
  schedule: Schedule;
  max_delivery_hours: number;
  capacity_basis: "mass_only" | "mass_and_volume";
}
export interface RailBatch {
  shipments: number;
  weight_tonnes: number;
  units: number;
  first_mile_vehicles: number;
  last_mile_vehicles: number;
  ready_at: string;
  rail_departure_at: string;
  delivered_at: string;
  max_delivery_hours: number;
}
export interface Scheme {
  scheme: string;
  status: "feasible" | "infeasible" | "insufficient_data";
  reason: string;
  missing_fields: string[];
  total_cost_rub: number | null;
  cost_rub_per_tonne: number | null;
  savings_vs_base_rub: number | null;
  savings_vs_base_percent: number | null;
  max_delivery_hours: number | null;
  departures: number | null;
  physical_trips: number | null;
  paid_trips: number | null;
  breakdown_rub: Record<string, number>;
  details: {
    vehicles_per_departure?: number;
    cargo_delivery_hours?: Record<string, number>;
    dispatch_at?: string[];
    fleet_policy?: string;
    billing_weeks?: {
      week_start: string;
      actual_trips: number;
      paid_trips: number;
    }[];
    group_size?: number;
    batches?: RailBatch[];
    search_scope?: string;
  };
  source_ids: string[];
}
export interface Calculation {
  schema_version: string;
  calculation_mode: string;
  request: CalculationRequest;
  dataset_id: string;
  data_status: string;
  shipments: number;
  total_volume_tonnes: number;
  planning_period: { start_at: string; end_at: string; end_exclusive: boolean };
  ready_at: string[];
  volume_by_week: {
    week_start: string;
    timezone: string;
    shipments: number;
    tonnes: number;
  }[];
  load_profile: {
    leg: number;
    origin: string;
    destination: string;
    weight_tonnes: number;
    volume_m3: number | null;
    cargo_ids: string[];
  }[];
  schemes: Scheme[];
  recommendation: string | null;
  recommendation_scope: string;
  assumptions: string[];
  warnings: string[];
  sources: { id: string; status: string; date?: string; description: string }[];
}
export const apiBase = (
  import.meta.env.VITE_API_URL ||
  `${window.location.protocol}//${window.location.hostname}:8000`
).replace(/\/$/, "");
export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
    public code: string,
    public fields: { field: string; message: string }[] = [],
    public retryAfter = 0,
  ) {
    super(message);
  }
}
export async function request<T>(
  path: string,
  init: RequestInit = {},
  timeoutMs = 30000,
): Promise<T> {
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), timeoutMs);
  const abort = () => controller.abort();
  init.signal?.addEventListener("abort", abort, { once: true });
  if (init.signal?.aborted) controller.abort();
  try {
    const response = await fetch(apiBase + path, {
      ...init,
      credentials: "include",
      signal: controller.signal,
    });
    if (response.status === 204) return undefined as T;
    const body = await response.json().catch(() => null);
    if (response.ok) {
      if (body === null)
        throw new Error("Сервер вернул пустой ответ. Попробуйте ещё раз.");
      return body as T;
    }
    const error = new ApiError(
      body?.error?.message ||
        `Ошибка сервера (${response.status}). Попробуйте ещё раз.`,
      response.status,
      body?.error?.code || "http_error",
      body?.error?.fields || [],
      Number(response.headers.get("Retry-After")) || 0,
    );
    if (
      response.status === 401 &&
      error.code !== "invalid_credentials" &&
      path !== "/auth/me"
    )
      window.dispatchEvent(new Event("session-expired"));
    throw error;
  } catch (error) {
    if (init.signal?.aborted) throw error;
    if (controller.signal.aborted)
      throw new Error(`Сервер не ответил за ${timeoutMs / 1000} секунд. Попробуйте ещё раз.`);
    if (error instanceof TypeError)
      throw new Error(
        "Не удалось связаться с сервером. Проверьте подключение и повторите попытку.",
      );
    throw error;
  } finally {
    window.clearTimeout(timeout);
    init.signal?.removeEventListener("abort", abort);
  }
}
export const post = <T>(path: string, body?: unknown, signal?: AbortSignal, timeoutMs?: number) =>
  request<T>(path, {
    method: "POST",
    signal,
    headers: { "Content-Type": "application/json" },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  }, timeoutMs);
export function errorText(error: unknown) {
  if (!(error instanceof Error))
    return "Не удалось выполнить действие. Попробуйте ещё раз.";
  if (error instanceof ApiError && error.fields.length) {
    const names: Record<string, string> = {
      email: "Почта",
      password: "Пароль",
      name: "Имя",
      code: "Код",
    };
    return `${error.message}. ${error.fields.map((f) => `${names[f.field] || f.field}: ${f.message.replace("Value error, ", "")}`).join("; ")}`;
  }
  return error.message;
}
