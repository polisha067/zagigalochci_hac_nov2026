import { apiBase } from "./api";
import { isNetwork, type Snapshot } from "./model";
const key = (userId: string) => `poputno:history:v1:${apiBase}:${userId}`;
export function loadHistory(userId: string): {
  entries: Snapshot[];
  warning: string;
} {
  try {
    const raw = localStorage.getItem(key(userId));
    if (!raw) return { entries: [], warning: "" };
    const entries = JSON.parse(raw) as Snapshot[];
    if (
      !Array.isArray(entries) ||
      entries.some(
        (e) =>
          !e.id ||
          !Number.isFinite(Date.parse(e.createdAt)) ||
          !e.result?.request ||
          (isNetwork(e)
            ? e.result.engine !== "russia" ||
              !Array.isArray(e.result.candidates) ||
              !Array.isArray(e.result.request.cargoes) ||
              !Array.isArray(e.result.request.stops)
            : !e.result.request.route?.length ||
              !Array.isArray(e.result.schemes)) ||
          !e.draft ||
          !e.cargoNames,
      )
    )
      throw new Error("invalid");
    return { entries, warning: "" };
  } catch {
    return {
      entries: [],
      warning:
        "Не удалось прочитать локальную историю. Сохранённые данные не изменены.",
    };
  }
}
export function saveSnapshot(
  userId: string,
  snapshot: Snapshot,
): { entries: Snapshot[]; warning: string } {
  const previous = loadHistory(userId);
  if (previous.warning)
    return {
      entries: [snapshot],
      warning:
        previous.warning + " Новый расчёт доступен до закрытия страницы.",
    };
  const entries = [
    snapshot,
    ...previous.entries.filter((e) => e.id !== snapshot.id),
  ];
  try {
    localStorage.setItem(key(userId), JSON.stringify(entries));
    return { entries, warning: "" };
  } catch {
    return {
      entries,
      warning:
        "Расчёт выполнен, но браузер не сохранил историю: хранилище недоступно или заполнено. Этот результат доступен до закрытия страницы.",
    };
  }
}

// Keep only the current result in this tab, including guest calculations.
// Persistent account history remains in localStorage and still requires sign-in.
const activeKey = (userId?: string) => `poputno:active:v1:${apiBase}:${userId ?? "guest"}`;
export function loadActiveSnapshot(userId?: string): Snapshot | null {
  try {
    const snapshot = JSON.parse(sessionStorage.getItem(activeKey(userId)) || "null") as Snapshot | null;
    if (!snapshot || !snapshot.id || !snapshot.result?.request || !snapshot.draft || !snapshot.cargoNames ||
        !Number.isFinite(Date.parse(snapshot.createdAt))) return null;
    if (isNetwork(snapshot)) {
      if (snapshot.result.engine !== "russia" || !Array.isArray(snapshot.result.candidates) ||
          !Array.isArray(snapshot.result.request.cargoes) || !Array.isArray(snapshot.result.request.stops)) return null;
    } else if (!snapshot.result.request.route?.length || !Array.isArray(snapshot.result.schemes)) return null;
    return snapshot;
  } catch { return null; }
}
export function storeActiveSnapshot(userId: string | undefined, snapshot: Snapshot | null) {
  try {
    if (snapshot) sessionStorage.setItem(activeKey(userId), JSON.stringify(snapshot));
    else sessionStorage.removeItem(activeKey(userId));
  } catch { /* A disabled/full session store must not block calculations or navigation. */ }
}
