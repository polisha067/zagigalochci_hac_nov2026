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
}
export interface CalculationRequest {
  engine: "russia";
  origin: string;
  destination: string;
  stops: Stop[];
  cargoes: CargoItem[];
  schedule: Schedule;
  max_delivery_hours: number;
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
  batch?: number;
  [key: string]: unknown;
}
export interface CostItem {
  kind: string;
  object_id?: string;
  amount_rub: number;
  at?: string;
  batch?: number;
  [key: string]: unknown;
}
export interface Delivery {
  cargo_instance_id: string;
  cargo_id: string;
  shipment: number;
  weight_tonnes: number;
  ready_at: string;
  delivered_at: string;
  delivery_hours: number;
  hub_id?: string;
}
export interface Candidate {
  id: string;
  service_ids: string[];
  modes: Mode[];
  group_size: number;
  multimodal: boolean;
  status: "feasible" | "infeasible" | "insufficient_data";
  complete: boolean;
  total_cost_rub: number | null;
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
    settlement_id: string;
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
}
