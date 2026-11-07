// Использовать после входа. Тот же сервер и cookie, что у createAuthClient.
export function createCalculationClient(apiBase = "http://localhost:8000") {
  async function request(path, data) {
    const response = await fetch(`${apiBase}${path}`, {
      method: data === undefined ? "GET" : "POST",
      credentials: "include",
      ...(data === undefined ? {} : {
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(data),
      }),
    });
    const body = await response.json();
    if (!response.ok) {
      const error = new Error(body.error?.message || "Ошибка расчёта");
      error.status = response.status;
      error.code = body.error?.code;
      error.fields = body.error?.fields || [];
      error.retryAfter = response.headers.get("Retry-After");
      throw error;
    }
    return body;
  }
  return {
    options: () => request("/calculation-options"),
    calculate: (parameters) => request("/calculations", parameters),
  };
}
