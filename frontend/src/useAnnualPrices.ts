import { useEffect, useMemo, useState } from "react";
import { errorText, post } from "./api";
import type { Calculation, Candidate } from "./networkApi";

type AnnualSeries = NonNullable<NonNullable<Candidate["forecast_price"]>["annual_series"]>;

/** Old history has a full ledger, but predates annual_series. Ask the same model to fill it. */
export function useAnnualPrices(candidate?: Candidate, forecast?: Calculation["forecast"]) {
  const budget = candidate?.forecast_price;
  const saved = budget?.annual_series;
  const complete = !!saved?.length && !!forecast && budget?.base_year != null &&
    saved.length === forecast.target_year - budget.base_year + 1 &&
    saved.every((point, index) => point.year === budget.base_year! + index);
  const payload = useMemo(() => {
    if (complete || !candidate?.complete || candidate.status !== "feasible" || !forecast ||
        budget?.base_year == null || budget.base_rub == null || candidate.total_cost_rub == null) return null;
    const charges = candidate.cost_items.map((item) => ({
      category: item.pricing?.category,
      effective_price_year: item.pricing?.effective_price_year,
      base_amount_rub: item.base_amount_rub,
    }));
    if (!charges.length || charges.some((item) => !item.category ||
        item.effective_price_year == null || item.base_amount_rub == null)) return null;
    return { base_year: budget.base_year, target_year: forecast.target_year,
      base_rub: budget.base_rub, total_rub: candidate.total_cost_rub,
      indices_version: forecast.indices_version, model_version: forecast.model_version, charges };
  }, [complete, candidate, forecast, budget]);
  const [state, setState] = useState<{ payload: typeof payload; series?: AnnualSeries; error?: string }>();
  useEffect(() => {
    if (!payload) return;
    const controller = new AbortController();
    post<{ annual_series: AnnualSeries }>("/network/russia/forecast-series", payload, controller.signal)
      .then(({ annual_series }) => setState({ payload, series: annual_series }))
      .catch((error) => {
        if (!controller.signal.aborted) setState({ payload, error: errorText(error) });
      });
    return () => controller.abort();
  }, [payload]);
  const current = state?.payload === payload ? state : undefined;
  return {
    series: complete ? saved : current?.series,
    loading: !!payload && !current,
    error: current?.error,
  };
}
