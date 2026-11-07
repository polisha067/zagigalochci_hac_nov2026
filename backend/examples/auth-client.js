// Можно импортировать в браузерный фронтенд. Сервер должен быть запущен.
// Передайте другой apiBase, если сервер работает на другой машине.
export function createAuthClient(apiBase = "http://localhost:8000") {
  async function request(path, method = "GET", data) {
    const response = await fetch(`${apiBase}${path}`, {
      method,
      credentials: "include",
      ...(data === undefined
        ? {}
        : {
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(data),
          }),
    });
    if (response.status === 204) return null;
    const body = await response.json();
    if (!response.ok) {
      const error = new Error(body.error?.message || "Ошибка запроса");
      error.status = response.status;
      error.code = body.error?.code;
      error.fields = body.error?.fields || [];
      error.retryAfter = response.headers.get("Retry-After");
      throw error;
    }
    return body;
  }
  return {
    register: ({ name, email, password }) =>
      request("/auth/register", "POST", { name, email, password }),
    login: ({ email, password }) =>
      request("/auth/login", "POST", { email, password }),
    me: () => request("/auth/me"),
    logout: () => request("/auth/logout", "POST"),
  };
}
