import type { Calculation, CalculationRequest, Options, Scheme } from "./api";
export const newCargo = () => ({
  id: crypto.randomUUID(),
  type: "",
  weight: "",
  from: "first",
  to: "last",
});
export const newStop = () => ({ id: crypto.randomUUID(), city: "" });
export type Cargo = ReturnType<typeof newCargo>;
export interface Draft {
  origin: string;
  destination: string;
  stops: ReturnType<typeof newStop>[];
  cargo: Cargo[];
  regular: boolean;
  count: string;
  weeks: string;
  start: string;
  days: string;
}
export const emptyDraft = (): Draft => ({
  origin: "",
  destination: "",
  stops: [],
  cargo: [newCargo()],
  regular: true,
  count: "",
  weeks: "",
  start: new Intl.DateTimeFormat("sv-SE", { timeZone: "Europe/Moscow" }).format(
    new Date(),
  ),
  days: "",
});
export interface Snapshot {
  id: string;
  createdAt: string;
  result: Calculation;
  draft: Draft;
  cargoNames: Record<string, string>;
}
export const num = (value: number | null | undefined, digits = 2) =>
  value == null || !Number.isFinite(value)
    ? "—"
    : new Intl.NumberFormat("ru-RU", { maximumFractionDigits: digits }).format(
        value,
      );
export const money = (value: number | null | undefined) =>
  value == null ? "—" : `${num(value)} ₽`;
export const hours = (value: number | null | undefined) =>
  value == null ? "—" : `${num(value, 6)} ч`;
export const date = (value: string, withTime = false) =>
  new Intl.DateTimeFormat("ru-RU", {
    timeZone: "Europe/Moscow",
    day: "numeric",
    month: "long",
    year: "numeric",
    ...(withTime ? { hour: "2-digit", minute: "2-digit" } : {}),
  }).format(new Date(value));
export const schemeName = (id: string) =>
  ({
    spot_road: "Разовые автозаказы",
    contract_road: "Авторейсы по договору",
    road_rail_road: "Авто → ж/д → авто",
  })[id] || id;
export function schemeStatus(s: Scheme, limit: number) {
  if (s.status === "insufficient_data") return "Недостаточно данных";
  if (s.status === "infeasible")
    return s.max_delivery_hours != null && s.max_delivery_hours > limit
      ? "Превышен срок"
      : "Есть ограничение";
  return "Соответствует условиям";
}
export const routeName = (r: Calculation) =>
  r.request.route.map((p) => p.city).join(" → ");
export function scheduleName(r: Calculation) {
  const s = r.request.schedule;
  if (s.mode === "once") return "Разовая отправка";
  if (s.mode === "explicit") return "Отправки по датам";
  const unit = { day: "дн.", week: "нед.", month: "мес." }[s.frequency.unit];
  return `${s.frequency.count} отправки за ${s.frequency.every} ${unit}`;
}
export function buildRequest(d: Draft, options: Options): CalculationRequest {
  const points = [
    { id: "first", city: d.origin },
    ...d.stops,
    { id: "last", city: d.destination },
  ];
  if (points.some((p) => !p.city))
    throw new Error("Выберите все пункты маршрута.");
  if (points.some((p, i) => i > 0 && p.city === points[i - 1].city))
    throw new Error("Соседние пункты маршрута должны различаться.");
  if (
    points.length > options.limits.route_points ||
    d.cargo.length > options.limits.cargoes
  )
    throw new Error("Превышено допустимое число пунктов или грузов.");
  const cargoes = d.cargo.map((c) => ({
    id: c.id,
    cargo_type: c.type,
    weight_tonnes: Number(c.weight),
    splittable: true,
    load_at: points.findIndex((p) => p.id === c.from),
    unload_at: points.findIndex((p) => p.id === c.to),
  }));
  if (
    !cargoes.length ||
    cargoes.some(
      (c) =>
        !c.cargo_type ||
        !Number.isFinite(c.weight_tonnes) ||
        c.weight_tonnes <= 0 ||
        c.weight_tonnes > 1000000,
    )
  )
    throw new Error("Укажите груз и положительный вес для каждой позиции.");
  if (cargoes.some((c) => c.load_at < 0 || c.load_at >= c.unload_at))
    throw new Error("Для каждого груза выгрузка должна идти после погрузки.");
  if (
    !cargoes.some((c) => c.load_at === 0) ||
    !cargoes.some((c) => c.unload_at === points.length - 1)
  )
    throw new Error(
      "Укажите груз для начального и конечного пунктов маршрута.",
    );
  if (!(Number(d.days) > 0 && Number(d.days) <= 3650))
    throw new Error(
      "Укажите максимальный срок от 0 до 3650 дней (больше нуля).",
    );
  const startAt = d.start + "T00:00:00+03:00";
  if (
    !Number.isFinite(Date.parse(startAt)) ||
    d.start < "2000-01-01" ||
    d.start > "2100-12-31"
  )
    throw new Error("Укажите дату начала в диапазоне 2000–2100 годов.");
  if (
    d.regular &&
    (!Number.isInteger(Number(d.count)) ||
      !Number.isInteger(Number(d.weeks)) ||
      Number(d.count) < 1 ||
      Number(d.count) > 168 ||
      Number(d.weeks) < 1 ||
      Number(d.weeks) * 7 > options.limits.horizon_days)
  )
    throw new Error(
      "Частота и число недель должны быть положительными целыми числами в допустимом диапазоне.",
    );
  if (d.regular && Number(d.count) * Number(d.weeks) > options.limits.shipments)
    throw new Error(
      `За период допустимо до ${options.limits.shipments} отправок. Сократите период или частоту.`,
    );
  const end = new Date(Date.parse(startAt) + Number(d.weeks) * 7 * 86400000);
  if (d.regular && end.getUTCFullYear() > 2100)
    throw new Error("Конец периода должен быть не позднее 2100 года.");
  return {
    dataset_id: options.dataset_id,
    route: points.map((p) => ({ city: p.city, service_hours: 0 })),
    cargoes,
    schedule: d.regular
      ? {
          mode: "regular",
          start_at: startAt,
          end_at: end.toISOString(),
          frequency: { count: Number(d.count), every: 1, unit: "week" },
        }
      : { mode: "once", ready_at: startAt },
    max_delivery_hours: Number(d.days) * 24,
    capacity_basis: "mass_only",
  };
}
