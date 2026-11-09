// Пользователь уже вошёл; API_URL должен совпадать с адресом его бэкенда.
export async function calculateNetwork(API_URL, request) {
  const response = await fetch(`${API_URL}/network/calculations`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ...request, engine: "network" }),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error?.message ?? `Ошибка ${response.status}`);
  return data; // Таблица и объяснение ИИ используют один и тот же data.
}
