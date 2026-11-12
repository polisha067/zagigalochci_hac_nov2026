export type Mode = "road" | "rail" | "air" | "sea" | "river" | "pipeline";
export type FrequencyUnit = "minute" | "hour" | "day" | "week" | "month";
export type Schedule =
  | { mode: "once"; ready_at: string }
  | {
      mode: "regular";
      start_at: string;
      end_at: string;
      frequency: { count: number; every: number; unit: FrequencyUnit };
    }
  | { mode: "explicit"; ready_at: string[] };
export interface Settlement {
  id: string;
  name: string;
  region?: string;
  latitude?: number | null;
  longitude?: number | null;
  provenance?: string;
}
export interface SettlementPage {
  network_id: string;
  fingerprint: string;
  total: number;
  offset: number;
  limit: number;
  settlements: Settlement[];
  coverage: Coverage;
  delivery_forecast?: ForecastOptions;
}
export interface ForecastOptions {
  min_year: number;
  max_year: number;
  base_year: number;
  scenario_modes: Mode[];
}
export interface NetworkOptions {
  cargo_types: { id: string; name: string; aliases: string[] }[];
  cargo_input: {
    mode: string;
    packagings: string[];
    handling_profiles: { id: string; name: string }[];
  };
  schedule_options: { modes: string[]; units: FrequencyUnit[] };
}
export interface Options extends NetworkOptions {
  settlements: Settlement[];
  coverage: Coverage;
  delivery_forecast?: ForecastOptions;
}
export interface CargoProperties {
  phase: "solid" | "liquid" | "gas";
  hazardous: boolean;
  handling_profile: string;
  temperature_control_required: boolean;
  required_temperature_c?: [number, number];
}
export interface CargoItem {
  id: string;
  name: string;
  weight_tonnes: number;
  volume_m3?: number;
  packaging?: string;
  splittable?: boolean;
  properties?: CargoProperties;
  max_piece_tonnes?: number;
  max_piece_dimensions_m?: [number, number, number];
  ready_offset_hours?: number;
  max_delivery_hours?: number;
  shelf_life_hours?: number;
}
export interface Stop {
  settlement: string;
  action: "visit" | "pickup" | "delivery" | "pickup_delivery";
  pickup_cargo_ids: string[];
  delivery_cargo_ids: string[];
  dwell_hours: number;
  no_reload?: boolean;
}
export interface CalculationRequest {
  engine: "russia";
  origin: string;
  destination: string;
  stops: Stop[];
  cargoes: CargoItem[];
  schedule: Schedule | null;
  max_delivery_hours: number | null;
  target_year?: number;
  capacity_basis: "mass_only" | "mass_and_volume";
  cost_basis: "including_vat" | "excluding_vat";
  data_policy: "allow_assumptions" | "verified_only";
  allowed_modes: Mode[];
  mass_basis?: "gross" | "net";
  max_candidates?: number;
  max_legs?: number;
}
export interface Clarification {
  cargo_id?: string;
  field?: string;
  fields?: string[];
  code: string;
  message: string;
}
export interface Issue {
  code: string;
  message: string;
  [key: string]: unknown;
}
export interface Step {
  kind: string;
  hub_id?: string;
  service_id?: string;
  mode?: Mode;
  from_hub?: string;
  to_hub?: string;
  cargo_ids?: string[];
  start_at?: string;
  end_at?: string;
  ready_at?: string;
  departure_at?: string;
  arrival_at?: string;
  weight_tonnes?: number;
  volume_m3?: number;
  units?: number;
  wait_hours?: number;
  duration_hours?: number;
  schedule_status?: "scenario" | "snapshot";
  departure_offset_hours?: number;
  arrival_offset_hours?: number;
  start_offset_hours?: number;
  end_offset_hours?: number;
  batch?: number;
  [key: string]: unknown;
}
export interface CostItem {
  kind: string;
  object_id?: string;
  amount_rub: number | null;
  at?: string;
  offset_hours?: number;
  base_amount_rub?: number;
  pricing?: {
    category: string; basis: string; quantity: number; rate_rub: number; base_year: number;
    source_ids: string[]; valid_from?: string; valid_until?: string;
    tariff_version?: string; observed_on?: string; tariff_date_precision?: "year"; tariff_projected?: boolean;
  };
  index_factors?: { year: number; factor: number; source_id: string; kind: string }[];
  index_proxy?: boolean;
  batch?: number;
  [key: string]: unknown;
}
export interface Delivery {
  cargo_instance_id: string;
  cargo_id: string;
  shipment: number;
  weight_tonnes: number;
  ready_at?: string;
  delivered_at?: string;
  ready_offset_hours?: number;
  delivered_offset_hours?: number;
  delivery_hours: number;
  hub_id?: string;
}
export interface ConnectionPilot {
  movement_rub: number;
  conditions: Record<string, string | null>;
  terminal_reference_url: string;
  truck_10t_reference_rub: number;
  truck_10t_forecast_rub: number | null;
  truck_10t_reference_url: string;
  scenarios: {
    scenario: "planned" | "within_buffer" | "requested" | "pickup_after_6h" | "pickup_after_24h";
    delay_hours: number;
    buffer_remaining_hours: number;
    missed: boolean;
    extra_hours: number | null;
    billable_truck_hours: number;
    billable_storage_days: number | null;
    known_rub: number;
    total_rub: number | null;
    charges_rub: Record<string, number | null>;
    missing: string[];
  }[];
}

export interface Candidate {
  id: string;
  service_ids: string[];
  modes: Mode[];
  group_size: number;
  multimodal: boolean;
  status: "feasible" | "infeasible" | "insufficient_data";
  complete: boolean;
  connection_pilot?: ConnectionPilot;
  total_cost_rub: number | null;
  known_cost_subtotal_rub?: number | null;
  cost_rub_per_tonne: number | null;
  max_delivery_hours: number | null;
  issues: Issue[];
  steps: Step[];
  deliveries: Delivery[];
  cost_items: CostItem[];
  source_ids: string[];
  evidence_levels: string[];
  route_hubs: {
    id: string;
    settlement_id: string | null;
    name: string;
    identity_status: string;
  }[];
  connections: {
    id: string;
    mode: Mode;
    kind: string;
    distance_km?: number | null;
    operational_status?: string;
    reference_metadata?: Record<string, unknown>;
  }[];
  stop_actions: (Stop & { hub_id: string; settlement_id?: string })[];
  booking_confirmed?: boolean;
  forecast_price?: {
    base_rub: number | null;
    base_year: number | null;
    lower_rub: number | null;
    upper_rub: number | null;
    known_subtotal_rub: number | null;
    air_base_rub?: number;
    air_median_rub?: number;
    air_softened_rub?: number | null;
    range_status: "available" | "not_calibrated" | "incomplete";
    long_term: boolean;
  };
  published_tariff_estimate?: {
    amount_excluding_vat_rub: number;
    missing_components: string[];
    scope: string;
  };
}
export interface Source {
  id: string;
  name?: string;
  description?: string;
  url?: string;
  status?: string;
  roles?: string[];
  notes?: string[];
  parameters?: Record<string, unknown>;
  metadata?: Record<string, unknown>;
  [key: string]: unknown;
}
export interface Coverage {
  settlements: number;
  observed_settlements: number;
  hubs: number;
  connections_by_mode: Record<string, number>;
  limitations: string[];
  geography: string;
  model: string;
  [key: string]: unknown;
}
export interface Calculation {
  schema_version: "1.0";
  engine: "russia";
  network_id: string;
  network_fingerprint: string;
  request: CalculationRequest;
  coverage: Coverage;
  settlements: (string | null)[];
  clarifications: Clarification[];
  candidates: Candidate[];
  recommendation: string | null;
  sources: Source[];
  input_status: "ready" | "needs_clarification";
  warnings: string[];
  shipments: number;
  status: string;
  search: { expansions: number; truncated: boolean; scope: string };
  stops: (Stop & { settlement_id: string })[];
  selected_subgraph: {
    hubs: number;
    services: number;
    transfers: number;
  } | null;
  forecast?: {
    target_year: number;
    base_year: number;
    date_precision: "year" | "date";
    alternative: { id: string; difference_rub: number; difference_hours: number; ranges_overlap: boolean | null } | null;
    assumptions: string[];
  };
}
