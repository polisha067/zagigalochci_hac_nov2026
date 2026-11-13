import type {
  Calculation,
  CalculationRequest,
  Candidate,
  CargoItem,
  FrequencyUnit,
  ForecastOptions,
  Mode,
  Options,
  Schedule,
  Settlement,
} from "./networkApi";
import type { Snapshot as LegacySnapshot } from "./legacyModel";
import { money } from "./legacyModel";
export { num, money, hours, date } from "./legacyModel";
export const airForecastFlightHours = (candidate: Pick<Candidate, "forecast_price" | "steps">) =>
  candidate.forecast_price?.air_median_rub == null
    ? null
    : candidate.steps.find((step) => step.kind === "transport" && step.mode === "air")?.duration_hours ?? null;
export const modeNames: Record<Mode, string> = {
  road: "Авто",
  rail: "Ж/д",
  air: "Авиа",
  sea: "Море",
  river: "Река",
  pipeline: "Трубопровод",
};
export const unitNames: Record<FrequencyUnit, string> = {
  minute: "мин.",
  hour: "ч.",
  day: "дн.",
  week: "нед.",
  month: "мес.",
};
export const packagingNames: Record<string, string> = {
  bags: "Мешки",
  boxes: "Коробки",
  pallets: "Паллеты",
  container: "Контейнер",
  container20: "Контейнер 20 футов",
  container40: "Контейнер 40 футов",
  bulk: "Навалом",
  bulk_solid: "Навалом / насыпью",
  bulk_liquid: "Наливом",
  drums: "Бочки",
  tank: "Цистерна",
  unpackaged: "Без упаковки",
};
export const newCargo = (id: string = crypto.randomUUID()) => ({
  id,
  name: "",
  weight: "",
  volume: "",
  packaging: "",
  splittable: "" as "" | "yes" | "no",
  from: "first",
  to: "last",
  phase: "" as "" | "solid" | "liquid" | "gas",
  hazardous: "" as "" | "yes" | "no",
  profile: "",
  temperatureRequired: "" as "" | "yes" | "no",
  minTemp: "",
  maxTemp: "",
  pieceWeight: "",
  length: "",
  width: "",
  height: "",
  readyOffset: "",
  cargoHours: "",
  shelfHours: "",
});
export type Cargo = ReturnType<typeof newCargo>;
export const newStop = () => ({
  id: crypto.randomUUID(),
  city: null as Settlement | null,
  dwell: "",
  noReload: false,
});
export interface Draft {
  origin: Settlement | null;
  destination: Settlement | null;
  stops: ReturnType<typeof newStop>[];
  cargo: Cargo[];
  scheduleMode: "once" | "regular" | "explicit";
  count: string;
  every: string;
  unit: FrequencyUnit;
  period: string;
  periodUnit: "hour" | "day" | "week" | "month";
  start: string;
  dates: string[];
  days: string;
  forecastYear?: string;
  capacityBasis: "mass_only" | "mass_and_volume";
  costBasis: "including_vat" | "excluding_vat";
  dataPolicy: "allow_assumptions" | "verified_only";
  modes: Mode[];
  massBasis: "" | "gross" | "net";
}
export const emptyDraft = (): Draft => ({
  origin: null,
  destination: null,
  stops: [],
  cargo: [newCargo()],
  scheduleMode: "regular",
  count: "",
  every: "1",
  unit: "week",
  period: "",
  periodUnit: "week",
  start:
    new Intl.DateTimeFormat("sv-SE", { timeZone: "Europe/Moscow" }).format(
      new Date(),
    ) + "T00:00",
  dates: [""],
  days: "",
  capacityBasis: "mass_only",
  costBasis: "including_vat",
  dataPolicy: "allow_assumptions",
  modes: ["road", "rail", "air", "sea", "river", "pipeline"],
  massBasis: "",
});
export interface NetworkSnapshot {
  kind: "russia";
  id: string;
  createdAt: string;
  result: Calculation;
  draft: Draft;
  cargoNames: Record<string, string>;
}
export type Snapshot = NetworkSnapshot | LegacySnapshot;
export function isNetwork(s: Snapshot): s is NetworkSnapshot {
  return "kind" in s && s.kind === "russia";
}
export function routeName(r: Snapshot["result"]) {
  return "engine" in r
    ? [
        r.request.origin,
        ...r.request.stops.map((s) => s.settlement),
        r.request.destination,
      ].join(" → ")
    : r.request.route.map((p) => p.city).join(" → ");
}
export function snapshotRoute(s: Snapshot) {
  return isNetwork(s)
    ? [
        s.draft.origin?.name || s.result.request.origin,
        ...s.draft.stops.map((p) => p.city?.name || ""),
        s.draft.destination?.name || s.result.request.destination,
      ].join(" → ")
    : routeName(s.result);
}
export function totalVolume(r: Snapshot["result"]) {
  return "engine" in r
    ? r.request.cargoes.reduce((sum, c) => sum + c.weight_tonnes, 0) *
        r.shipments
    : r.total_volume_tonnes;
}
export function cargoLabel(s: Snapshot) {
  return isNetwork(s)
    ? s.result.request.cargoes.map((c) => c.name).join(", ")
    : s.result.request.cargoes
        .map((c) => s.cargoNames[c.cargo_type] || c.cargo_type)
        .join(", ");
}
export function scheduleName(schedule: Schedule) {
  return schedule.mode === "once"
    ? "Разовая отправка"
    : schedule.mode === "explicit"
      ? "Отправки по выбранным датам"
      : `${schedule.frequency.count} отправки каждые ${schedule.frequency.every} ${unitNames[schedule.frequency.unit]}`;
}
export function candidateName(c: { modes: Mode[] }) {
  return c.modes.map((m) => modeNames[m]).join(" → ");
}
export function toMoscowISO(value: string) {
  if (
    !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(value) ||
    value.slice(0, 4) < "2000" ||
    value.slice(0, 4) > "2100"
  )
    throw new Error("Укажите дату и время в диапазоне 2000–2100 годов.");
  const ms = Date.parse(value + ":00+03:00");
  if (!Number.isFinite(ms)) throw new Error("Проверьте дату и время.");
  return new Date(ms).toISOString();
}
const HOUR = 3600000;
function addMonths(time: number, n: number) {
  const local = new Date(time + 3 * HOUR);
  const day = local.getUTCDate();
  local.setUTCDate(1);
  local.setUTCMonth(local.getUTCMonth() + n);
  const last = new Date(
    Date.UTC(local.getUTCFullYear(), local.getUTCMonth() + 1, 0),
  ).getUTCDate();
  local.setUTCDate(Math.min(day, last));
  return local.getTime() - 3 * HOUR;
}
function addPeriod(time: number, n: number, unit: FrequencyUnit) {
  return unit === "month"
    ? addMonths(time, n)
    : time +
        n *
          { minute: HOUR / 60, hour: HOUR, day: 24 * HOUR, week: 168 * HOUR }[
            unit
          ];
}
export function buildSchedule(d: Draft): Schedule {
  if (d.scheduleMode === "explicit") {
    const dates = d.dates.map(toMoscowISO).sort();
    if (!dates.length || dates.length > 64)
      throw new Error("Укажите от 1 до 64 дат отправки.");
    if (new Set(dates).size !== dates.length)
      throw new Error("Даты отправки не должны повторяться.");
    if (Date.parse(dates.at(-1)!) - Date.parse(dates[0]) > 3660 * 24 * HOUR)
      throw new Error("Период не должен превышать 3660 дней.");
    return { mode: "explicit", ready_at: dates };
  }
  const start = toMoscowISO(d.start);
  if (d.scheduleMode === "once") return { mode: "once", ready_at: start };
  if (
    !Number.isInteger(Number(d.count)) ||
    Number(d.count) < 1 ||
    Number(d.count) > 168 ||
    !Number.isInteger(Number(d.every)) ||
    Number(d.every) < 1 ||
    Number(d.every) > 3650 ||
    !Number.isInteger(Number(d.period)) ||
    Number(d.period) < 1
  )
    throw new Error(
      "Укажите положительные целые значения частоты, интервала и периода. Частота — до 168 отправок, интервал — до 3650 единиц.",
    );
  const end = addPeriod(Date.parse(start), Number(d.period), d.periodUnit);
  if (
    !Number.isFinite(end) ||
    end <= Date.parse(start) ||
    end - Date.parse(start) > 3660 * 24 * HOUR ||
    new Date(end + 3 * HOUR).getUTCFullYear() > 2100
  )
    throw new Error(
      "Проверьте период: не более 3660 дней, конец не позднее 2100 года.",
    );
  return {
    mode: "regular",
    start_at: start,
    end_at: new Date(end).toISOString(),
    frequency: { count: Number(d.count), every: Number(d.every), unit: d.unit },
  };
}
export function scheduleEvents(s: Schedule): string[] {
  if (s.mode === "once") return [s.ready_at];
  if (s.mode === "explicit") return s.ready_at;
  const events: string[] = [];
  const start = Date.parse(s.start_at);
  const end = Date.parse(s.end_at);
  for (let index = 0; ; index++) {
    const left = addPeriod(start, index * s.frequency.every, s.frequency.unit);
    if (left >= end) break;
    const right = addPeriod(
      start,
      (index + 1) * s.frequency.every,
      s.frequency.unit,
    );
    for (let pos = 0; pos < s.frequency.count; pos++) {
      const time = left + ((right - left) * pos) / s.frequency.count;
      if (time < end) {
        events.push(new Date(time).toISOString());
        if (events.length > 64)
          throw new Error(
            "За один сетевой расчёт допустимо до 64 отправок. Сократите период или частоту.",
          );
      }
    }
  }
  return events;
}

export function scheduleDraft(schedule: Schedule, readyDates?: string[]): Partial<Draft> {
  const moscowInput = (value: string) =>
    new Date(Date.parse(value) + 3 * HOUR).toISOString().slice(0, 16);
  if (schedule.mode === "once") {
    return { scheduleMode: "once", start: moscowInput(schedule.ready_at) };
  }
  if (schedule.mode === "explicit") {
    return { scheduleMode: "explicit", dates: schedule.ready_at.map(moscowInput) };
  }
  const start = Date.parse(schedule.start_at);
  const end = Date.parse(schedule.end_at);
  const from = new Date(start + 3 * HOUR);
  const to = new Date(end + 3 * HOUR);
  const months = (to.getUTCFullYear() - from.getUTCFullYear()) * 12 + to.getUTCMonth() - from.getUTCMonth();
  const elapsedHours = (end - start) / HOUR;
  const periods: [Draft["periodUnit"], number][] = [
    ["week", elapsedHours / 168], ["month", months], ["day", elapsedHours / 24], ["hour", elapsedHours],
  ];
  const period = periods.find(([unit, value]) =>
    Number.isInteger(value) && value > 0 && addPeriod(start, value, unit) === end,
  );
  if (!period) {
    return { scheduleMode: "explicit", dates: (readyDates ?? scheduleEvents(schedule)).map(moscowInput) };
  }
  return {
    scheduleMode: "regular",
    start: moscowInput(schedule.start_at),
    count: String(schedule.frequency.count),
    every: String(schedule.frequency.every),
    unit: schedule.frequency.unit,
    period: String(period[1]),
    periodUnit: period[0],
  };
}
function positive(value: string, label: string, maximum = 1000000) {
  const n = Number(value);
  if (!Number.isFinite(n) || n <= 0 || n > maximum)
    throw new Error(
      `${label}: укажите положительное число не больше ${maximum}.`,
    );
  return n;
}
export function selectedForecastYear(d: Draft, options?: ForecastOptions): number | undefined {
  if (!options) return undefined;
  const date = d.scheduleMode === "explicit" ? d.dates.reduce((last, value) => value > last ? value : last, "") : d.start;
  const year = Number(d.forecastYear || date?.slice(0, 4));
  return year >= options.min_year ? year : undefined;
}

export function forecastMoney(value: number | null | undefined, longTerm = false) {
  if (value == null) return money(value);
  const step = longTerm ? 1000 : 100;
  return value >= step ? money(Math.round(value / step) * step) : money(value);
}

export function buildRequest(d: Draft, forecastOptions?: ForecastOptions): CalculationRequest {
  const points = [
    { id: "first", city: d.origin },
    ...d.stops,
    { id: "last", city: d.destination },
  ];
  if (points.some((p) => !p.city))
    throw new Error("Выберите все населённые пункты из подсказок.");
  if (new Set(points.map((p) => p.city!.id)).size !== points.length)
    throw new Error("Начало, остановки и конец маршрута должны различаться.");
  if (d.stops.length > 8 || !d.cargo.length || d.cargo.length > 20)
    throw new Error("Допустимо до 8 остановок и 20 грузов.");
  const cargoes: CargoItem[] = d.cargo.map((c, index) => {
    if (!c.name.trim() || c.name.trim().length > 100)
      throw new Error(
        `Позиция ${index + 1}: укажите название груза до 100 символов.`,
      );
    const from = points.findIndex((p) => p.id === c.from),
      to = points.findIndex((p) => p.id === c.to);
    if (from < 0 || to < 0 || from >= to)
      throw new Error(
        `Позиция ${index + 1}: выгрузка должна быть после погрузки.`,
      );
    const item: CargoItem = {
      id: c.id,
      name: c.name.trim(),
      weight_tonnes: positive(c.weight, "Вес груза"),
    };
    if (c.volume) item.volume_m3 = positive(c.volume, "Объём груза");
    if (d.capacityBasis === "mass_and_volume" && item.volume_m3 == null)
      throw new Error(
        "Для проверки массы и объёма укажите объём каждой позиции.",
      );
    if (c.packaging) item.packaging = c.packaging;
    if (c.splittable) item.splittable = c.splittable === "yes";
    const props = [
      c.phase,
      c.hazardous,
      c.profile.trim(),
      c.temperatureRequired,
    ];
    if (props.some(Boolean) && !props.every(Boolean))
      throw new Error(
        `Позиция ${index + 1}: заполните состояние груза, опасность, условия обращения и необходимость температурного режима.`,
      );
    if (props.every(Boolean)) {
      item.properties = {
        phase: c.phase as "solid" | "liquid" | "gas",
        hazardous: c.hazardous === "yes",
        handling_profile: c.profile.trim(),
        temperature_control_required: c.temperatureRequired === "yes",
      };
      if (c.temperatureRequired === "yes" && (c.minTemp || c.maxTemp)) {
        if (
          c.minTemp === "" ||
          c.maxTemp === "" ||
          !Number.isFinite(Number(c.minTemp)) ||
          !Number.isFinite(Number(c.maxTemp)) ||
          Number(c.minTemp) > Number(c.maxTemp)
        )
          throw new Error("Укажите корректный диапазон температур.");
        item.properties.required_temperature_c = [
          Number(c.minTemp),
          Number(c.maxTemp),
        ];
      }
    }
    if (c.pieceWeight) {
      item.max_piece_tonnes = positive(c.pieceWeight, "Масса одного места");
      if (item.max_piece_tonnes > item.weight_tonnes)
        throw new Error("Масса одного места не может превышать массу позиции.");
    }
    if ([c.length, c.width, c.height].some(Boolean)) {
      if (![c.length, c.width, c.height].every(Boolean))
        throw new Error("Укажите все три размера грузового места.");
      item.max_piece_dimensions_m = [
        positive(c.length, "Длина"),
        positive(c.width, "Ширина"),
        positive(c.height, "Высота"),
      ];
    }
    if (c.readyOffset) {
      const n = Number(c.readyOffset);
      if (!Number.isFinite(n) || n < 0 || n > 87600)
        throw new Error("Проверьте время готовности груза.");
      item.ready_offset_hours = n;
    }
    if (c.cargoHours)
      item.max_delivery_hours = positive(c.cargoHours, "Срок позиции", 87600);
    if (c.shelfHours)
      item.shelf_life_hours = positive(c.shelfHours, "Срок годности", 87600);
    return item;
  });
  if (
    !d.cargo.some((c) => c.from === "first") ||
    !d.cargo.some((c) => c.to === "last")
  )
    throw new Error(
      "Нужна погрузка в начале маршрута и выгрузка в конечном пункте.",
    );
  if (!d.modes.length) throw new Error("Выберите хотя бы один вид транспорта.");
  const year = selectedForecastYear(d, forecastOptions);
  if (year && forecastOptions && year > forecastOptions.max_year)
    throw new Error(`Прогноз по текущим данным доступен до ${forecastOptions.max_year} года.`);
  const schedule = year && d.scheduleMode === "once" && !d.start ? null : buildSchedule(d);
  if (schedule) scheduleEvents(schedule);
  const max_delivery_hours =
    year && !d.days ? null : positive(d.days, "Максимальный срок, дней", 3650) * 24;
  return {
    engine: "russia",
    origin: d.origin!.id,
    destination: d.destination!.id,
    stops: d.stops.map((s) => {
      const pickup = d.cargo.filter((c) => c.from === s.id).map((c) => c.id),
        delivery = d.cargo.filter((c) => c.to === s.id).map((c) => c.id);
      const dwell = s.dwell === "" ? 0 : Number(s.dwell);
      if (!Number.isFinite(dwell) || dwell < 0 || dwell > 87600)
        throw new Error("Проверьте дополнительное время остановки.");
      if (s.noReload && (pickup.length || delivery.length))
        throw new Error("Для остановки без перегрузки уберите погрузку и выгрузку грузовых позиций.");
      return {
        settlement: s.city!.id,
        action:
          pickup.length && delivery.length
            ? "pickup_delivery"
            : pickup.length
              ? "pickup"
              : delivery.length
                ? "delivery"
                : "visit",
        pickup_cargo_ids: pickup,
        delivery_cargo_ids: delivery,
        dwell_hours: dwell,
        no_reload: Boolean(s.noReload),
      };
    }),
    cargoes,
    schedule,
    max_delivery_hours,
    ...(year ? { target_year: year } : {}),
    capacity_basis: d.capacityBasis,
    cost_basis: d.costBasis,
    data_policy: year ? "allow_assumptions" : d.dataPolicy,
    allowed_modes: d.modes,
    ...(d.massBasis ? { mass_basis: d.massBasis } : {}),
    max_candidates: 8,
    max_legs: year ? 8 : 12,
  };
}
export function editableDraft(s: Snapshot, options?: Options): Draft {
  if (isNetwork(s)) return structuredClone(s.draft);
  const request = s.result.request;
  const d = { ...emptyDraft(), ...scheduleDraft(request.schedule, s.result.ready_at) };
  const lookup = (name: string): Settlement =>
    options?.settlements.find((c) => c.name === name) || { id: name, name };
  d.origin = lookup(request.route[0].city);
  d.destination = lookup(request.route[request.route.length - 1].city);
  d.stops = request.route.slice(1, -1).map((p) => ({
    ...newStop(),
    city: lookup(p.city),
    dwell: String(p.service_hours),
  }));
  const points = ["first", ...d.stops.map((s) => s.id), "last"];
  d.cargo = request.cargoes.map((c) => ({
    ...newCargo(c.id),
    name: s.cargoNames[c.cargo_type] || c.cargo_type,
    weight: String(c.weight_tonnes),
    volume: c.volume_m3 == null ? "" : String(c.volume_m3),
    from: points[c.load_at],
    to: points[c.unload_at ?? points.length - 1],
    splittable: c.splittable ? "yes" : "no",
  }));
  d.capacityBasis = request.capacity_basis;
  d.days = String(request.max_delivery_hours / 24);
  return d;
}
