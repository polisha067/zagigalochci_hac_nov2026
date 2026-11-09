import type { Calculation as PilotCalculation } from "./api";
import type { Calculation as RussiaCalculation, Candidate } from "./networkApi";

export type AssistantDraft = Record<string, unknown>;
export interface PendingTool {
  tool: string;
  arguments: Record<string, unknown>;
}
export interface AssistantSource {
  title: string;
  url: string | null;
  path: string | null;
  section: string | null;
  date: string | null;
}
export interface ReferenceResult {
  dataset_id: string;
  status: "available" | "unavailable";
  rows: Record<string, unknown>[];
  assumptions: string[];
  details: Record<string, unknown>;
}
export interface NetworkCalculation {
  engine: "network";
  request: { origin_hub_id: string; destination_hub_id: string; cost_basis: string };
  shipments: number;
  total_volume_tonnes: number;
  candidates: Pick<Candidate, "id" | "modes" | "status" | "total_cost_rub" | "max_delivery_hours" | "issues">[];
  recommendation: string | null;
}
export interface AirCalculation {
  engine: "chukotavia";
  request: { origin: string; destination: string };
  overview: { shipment_count: number; chargeable_weight_kg_per_shipment: string; cost_basis: string };
  shipments: {
    number: number;
    ready_at: string;
    candidates: {
      id: string;
      flight: string;
      departure_at: string;
      arrival_at: string;
      transport_component_estimate_rub: number | null;
      scenario_delivery_hours: number | null;
    }[];
  }[];
}
export interface PipelineCalculation {
  engine: "pipeline";
  request: { route_id: string; net_weight_tonnes: number; shipment_at: string };
  known_components: { route_id: string; rate_rub: string; amount_excluding_vat_rub: number }[];
}
export type AssistantCalculation = PilotCalculation | RussiaCalculation | NetworkCalculation | AirCalculation | PipelineCalculation;

interface ReplyBase {
  reply: string;
  draft: AssistantDraft | null;
  pending: PendingTool | null;
  missing_fields: string[];
  sources: AssistantSource[];
  tool: string | null;
  engine: "pilot" | "network" | "russia" | "chukotavia" | "pipeline" | null;
}
export type AssistantReply = ReplyBase & (
  | { kind: "calculation"; calculation: AssistantCalculation; result: null }
  | { kind: "information"; calculation: null; result: ReferenceResult | null }
  | { kind: "clarification"; calculation: AssistantCalculation | null; result: null }
);
export type DashboardReply = Extract<AssistantReply, { kind: "calculation" | "information" }>;
