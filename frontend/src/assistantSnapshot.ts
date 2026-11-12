import type { DashboardCalculation } from "./assistantApi";
import { emptyDraft, newCargo, newStop, scheduleDraft, type Cargo, type Snapshot } from "./model";
import type { Options } from "./networkApi";

export function assistantSnapshot(result: DashboardCalculation, options: Options | null, names: Record<string, string>): Snapshot {
  const metadata = {
    id: crypto.randomUUID(),
    createdAt: new Date().toISOString(),
    cargoNames: Object.fromEntries((options?.cargo_types ?? []).map((cargo) => [cargo.id, cargo.name])),
  };
  const { request } = result;
  const settlement = (value: string, index: number) => {
    const id = result.settlements[index] ?? value;
    return options?.settlements.find((city) => city.id === id) ?? { id, name: names[id] ?? value };
  };
  const stops = request.stops.map((stop, index) => ({
    ...newStop(),
    city: settlement(stop.settlement, index + 1),
    dwell: String(stop.dwell_hours),
    noReload: Boolean(stop.no_reload),
  }));
  const pickup = new Map(request.stops.flatMap((stop, index) =>
    stop.pickup_cargo_ids.map((id) => [id, stops[index].id] as const),
  ));
  const delivery = new Map(request.stops.flatMap((stop, index) =>
    stop.delivery_cargo_ids.map((id) => [id, stops[index].id] as const),
  ));
  const field = (value: number | null | undefined) => value == null ? "" : String(value);
  const yesNo = (value: boolean | null | undefined): "" | "yes" | "no" =>
    value == null ? "" : value ? "yes" : "no";

  return {
    ...metadata,
    kind: "russia",
    result,
    draft: {
      ...emptyDraft(),
      ...(request.schedule ? scheduleDraft(request.schedule) : { scheduleMode: "once", start: "" }),
      forecastYear: result.forecast ? String(result.forecast.target_year) : undefined,
      origin: settlement(request.origin, 0),
      destination: settlement(request.destination, stops.length + 1),
      stops,
      cargo: request.cargoes.map<Cargo>((cargo) => ({
        ...newCargo(cargo.id),
        name: cargo.name,
        weight: field(cargo.weight_tonnes),
        volume: field(cargo.volume_m3),
        packaging: cargo.packaging ?? "",
        splittable: yesNo(cargo.splittable),
        from: pickup.get(cargo.id) ?? "first",
        to: delivery.get(cargo.id) ?? "last",
        phase: cargo.properties?.phase ?? "",
        hazardous: yesNo(cargo.properties?.hazardous),
        profile: cargo.properties?.handling_profile ?? "",
        temperatureRequired: yesNo(cargo.properties?.temperature_control_required),
        minTemp: field(cargo.properties?.required_temperature_c?.[0]),
        maxTemp: field(cargo.properties?.required_temperature_c?.[1]),
        pieceWeight: field(cargo.max_piece_tonnes),
        length: field(cargo.max_piece_dimensions_m?.[0]),
        width: field(cargo.max_piece_dimensions_m?.[1]),
        height: field(cargo.max_piece_dimensions_m?.[2]),
        readyOffset: field(cargo.ready_offset_hours),
        cargoHours: field(cargo.max_delivery_hours),
        shelfHours: field(cargo.shelf_life_hours),
      })),
      days: request.max_delivery_hours == null ? "" : String(request.max_delivery_hours / 24),
      capacityBasis: request.capacity_basis,
      costBasis: request.cost_basis,
      dataPolicy: request.data_policy,
      modes: request.allowed_modes,
      massBasis: request.mass_basis ?? "",
    },
  };
}
