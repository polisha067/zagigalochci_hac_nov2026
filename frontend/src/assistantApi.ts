import type { Calculation } from "./networkApi";

export type DashboardCalculation = Calculation;

export interface AssistantSource {
  title: string;
  url: string | null;
  path: string | null;
  section: string | null;
  date: string | null;
}

interface ReplyBase {
  reply: string;
  missing_fields: string[];
  sources: AssistantSource[];
}

export type AssistantReply = ReplyBase & (
  | { kind: "calculation"; calculation: Calculation; conditional?: boolean }
  | { kind: "information"; calculation: null }
  | { kind: "clarification"; calculation: Calculation | null }
);

export function dashboardResult(answer: AssistantReply): Calculation | null {
  const result = answer.calculation;
  return result?.engine === "russia" && result.input_status === "ready" ? result : null;
}

export interface DialogScenario {
  title: string;
  answer: AssistantReply;
}

export interface AssistantDialog {
  revision: number;
  answer: AssistantReply | null;
  known: string[];
  changes: string[];
  scenarios: DialogScenario[];
  selected_candidate: string | null;
  settlement_names: Record<string, string>;
}

export interface CorridorScheme {
  scheme: string;
  verdict: string;
  feasible: boolean;
  cost_rub: number | null;
  rub_per_tonne: number | null;
  worst_delivery_hours: number | null;
  cost_difference_vs_spot_rub?: number | null;
}

export interface CorridorReport {
  kind: "corridor";
  origin: string;
  destination: string;
  year: number;
  deadlineDays: number;
  forecastTonnes: number;
  schemes: CorridorScheme[];
  assumptions: string[];
  sources: AssistantSource[];
}
