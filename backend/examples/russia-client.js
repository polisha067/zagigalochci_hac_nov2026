// API examples for the frontend; the browser retains the existing HttpOnly session.
export async function findSettlements(apiBase, query) {
  const params = new URLSearchParams({ query, limit: '50' });
  const response = await fetch(`${apiBase}/network/russia/options?${params}`, {
    credentials: 'include',
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error?.message ?? 'Не удалось загрузить населённые пункты');
  return result;
}

export async function calculateRussia(apiBase, request) {
  const response = await fetch(`${apiBase}/network/russia/calculations`, {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ...request, engine: 'russia' }),
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error?.message ?? 'Не удалось выполнить расчёт');
  // needs_clarification: show the questions and retain the user's form.
  // candidate.total_cost_rub === null: do not format as zero or call it a full price.
  return result;
}
