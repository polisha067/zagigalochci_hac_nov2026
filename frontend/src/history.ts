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
