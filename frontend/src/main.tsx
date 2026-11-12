import { useCallback, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { ApiError, errorText, post, request, type User } from "./api";
import { editableDraft, emptyDraft, isNetwork, type Draft, type Snapshot } from "./model";
import type {
  Calculation,
  NetworkOptions,
  Options,
  SettlementPage,
} from "./networkApi";
import { loadHistory, saveSnapshot } from "./history";
import { Auth } from "./Auth";
import { ConditionsForm } from "./ConditionsForm";
import { ForecastOverview } from "./ForecastOverview";
import { History, Results } from "./Results";
import { Assistant } from "./Assistant";
import { assistantSnapshot } from "./assistantSnapshot";
import type { DashboardCalculation } from "./assistantApi";
import { Brand } from "./ui";
import "./style.css";

function navigate(hash: string) {
  if (window.location.hash !== hash) window.location.hash = hash;
  else window.scrollTo({ top: 0 });
}
function Workspace({
  user,
  onUser,
  authMessage,
  onLogout,
}: {
  user: User | null;
  onUser: (user: User) => void;
  authMessage: string;
  onLogout: () => void;
}) {
  const [options, setOptions] = useState<Options | null>(null);
  const [catalogError, setCatalogError] = useState("");
  const [catalogAttempt, setCatalogAttempt] = useState(0);
  const [draft, setDraft] = useState<Draft>(emptyDraft);
  const [formVersion, setFormVersion] = useState(0);
  const [current, setCurrent] = useState<Snapshot | null>(null);
  const [assistantSelection, setAssistantSelection] = useState<{ snapshot: string; candidate: string } | null>(null);
  const userId = user?.id;
  const [historyCache, setHistoryCache] = useState<{
    userId: string;
    state: ReturnType<typeof loadHistory>;
  } | null>(null);
  const historyState = userId
    ? historyCache?.userId === userId ? historyCache.state : loadHistory(userId)
    : { entries: [], warning: "" };
  const [hash, setHash] = useState(window.location.hash || "#calculation");
  const [logoutBusy, setLogoutBusy] = useState(false);
  const [logoutError, setLogoutError] = useState("");
  useEffect(() => {
    const update = () => {
      setHash(window.location.hash || "#calculation");
      window.scrollTo({ top: 0 });
    };
    window.addEventListener("hashchange", update);
    return () => window.removeEventListener("hashchange", update);
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    setCatalogError("");
    Promise.all([
      request<NetworkOptions>("/network/options", {
        signal: controller.signal,
      }),
      request<SettlementPage>("/network/russia/options?limit=50", {
        signal: controller.signal,
      }),
    ])
      .then(([catalog, locations]) =>
        setOptions({
          ...catalog,
          settlements: locations.settlements,
          coverage: locations.coverage,
          delivery_forecast: locations.delivery_forecast,
        }),
      )
      .catch((e) => {
        if (!controller.signal.aborted) setCatalogError(errorText(e));
      });
    return () => controller.abort();
  }, [catalogAttempt]);
  useEffect(() => {
    if (!userId) return;
    const sync = () => {
      const loaded = loadHistory(userId);
      setHistoryCache((previous) => ({
        userId,
        state: loaded.warning && previous?.userId === userId
          ? { ...previous.state, warning: loaded.warning }
          : loaded,
      }));
    };
    sync();
    window.addEventListener("storage", sync);
    return () => window.removeEventListener("storage", sync);
  }, [userId]);
  const inHistory = hash.startsWith("#history");
  const needsAuth = !user && (inHistory || hash === "#login");
  const calculationHash = current
    ? "#calculation/result/" + current.id
    : "#calculation";
  const saved = hash.startsWith("#history/")
    ? historyState.entries.find((s) => s.id === hash.slice("#history/".length))
    : undefined;
  const resultId = hash.startsWith("#calculation/result/")
    ? hash.slice("#calculation/result/".length)
    : null;
  const showing = inHistory
    ? saved || null
    : resultId
      ? (current?.id === resultId
          ? current
          : historyState.entries.find((s) => s.id === resultId)) || null
      : null;
  useEffect(() => {
    document.title = `${inHistory ? (saved ? "Сохранённый прогноз" : "История прогнозов") : showing ? "Результат прогноза" : "Прогноз грузоперевозок"} · Попутно`;
  }, [inHistory, saved, showing]);
  function newCalculation() {
    setDraft(emptyDraft());
    setFormVersion((v) => v + 1);
    setCurrent(null);
    navigate("#calculation");
  }
  function completed(result: Calculation, submitted: Draft) {
    const snapshot: Snapshot = {
      kind: "russia",
      id: crypto.randomUUID(),
      createdAt: new Date().toISOString(),
      result,
      draft: structuredClone(submitted),
      cargoNames: Object.fromEntries(
        (options?.cargo_types || []).map((c) => [c.id, c.name]),
      ),
    };
    setDraft(submitted);
    publishSnapshot(snapshot);
  }
  function publishSnapshot(snapshot: Snapshot) {
    setCurrent(snapshot);
    if (userId) {
      const savedHistory = saveSnapshot(userId, snapshot);
      setHistoryCache((previous) => ({
        userId,
        state: {
          ...savedHistory,
          entries: [
            ...new Map(
              [...savedHistory.entries, ...(previous?.userId === userId ? previous.state.entries : [])]
                .map((entry) => [entry.id, entry]),
            ).values(),
          ],
        },
      }));
    }
    navigate("#calculation/result/" + snapshot.id);
  }
  function showAssistantResult(result: DashboardCalculation, settlementNames: Record<string, string>, candidateId?: string) {
    const snapshot = assistantSnapshot(result, options, settlementNames);
    setAssistantSelection(candidateId ? { snapshot: snapshot.id, candidate: candidateId } : null);
    publishSnapshot(snapshot);
  }
  async function logout() {
    if (logoutBusy) return;
    setLogoutBusy(true);
    setLogoutError("");
    try {
      await post<void>("/auth/logout");
      navigate("#calculation");
      onLogout();
    } catch (error) {
      setLogoutError(errorText(error));
    } finally {
      setLogoutBusy(false);
    }
  }
  if (needsAuth) {
    return (
      <Auth
        initialMessage={authMessage || (inHistory ? "Войдите или зарегистрируйтесь, чтобы открыть историю расчётов." : "")}
        onBack={() => navigate(calculationHash)}
        onUser={(authenticatedUser) => {
          if (!inHistory) navigate(calculationHash);
          onUser(authenticatedUser);
        }}
      />
    );
  }
  return (
    <div className="page">
      <header className="app-header">
        <Brand />
        <div className="header-controls">
          <nav aria-label="Разделы">
            <button
              aria-current={!inHistory ? "page" : undefined}
              onClick={() => navigate(calculationHash)}
            >
              Прогноз
            </button>
            <button
              aria-current={inHistory ? "page" : undefined}
              onClick={() => navigate("#history")}
            >
              История
            </button>
          </nav>
          {user ? <details className="account">
            <summary aria-label={"Аккаунт: " + user.name}>
              <span>
                <b className="account-initials">
                  {user.name
                    .trim()
                    .split(/\s+/)
                    .slice(0, 2)
                    .map((n) => n[0])
                    .join("")
                    .toLocaleUpperCase("ru")}
                </b>
              </span>
            </summary>
            <div className="account-menu">
              <b>{user.name}</b>
              <small>{user.email}</small>
              <button
                className="text-button"
                onClick={logout}
                disabled={logoutBusy}
              >
                {logoutBusy ? "Выходим…" : "Выйти"}
              </button>
            </div>
          </details> : (
            <button className="text-button" onClick={() => navigate("#login")}>
              Войти
            </button>
          )}
        </div>
      </header>
      <main className="wide-main forecast-main">
        {logoutError && (
          <p role="alert" className="feedback error">
            {logoutError}
          </p>
        )}
        {historyState.warning && (
          <p role="alert" className="feedback warning">
            {historyState.warning}
          </p>
        )}
        {showing ? (
          <Results
            key={showing.id + (inHistory ? "-saved" : "")}
            snapshot={showing}
            initialCandidate={assistantSelection?.snapshot === showing.id ? assistantSelection.candidate : undefined}
            saved={inHistory}
            editor={!inHistory && options && isNetwork(showing) ? (
              <ConditionsForm key={showing.id} initial={editableDraft(showing, options)}
                options={options} onDraft={setDraft} onResult={completed} />
            ) : undefined}
            draftChanged={isNetwork(showing) && JSON.stringify(draft) !== JSON.stringify(showing.draft)}
            onNew={newCalculation}
            onEdit={() => {
              setDraft(editableDraft(showing, options || undefined));
              setFormVersion((v) => v + 1);
              setCurrent(null);
              navigate("#calculation");
            }}
            onBack={() => navigate("#history")}
          />
        ) : inHistory ? (
          <>
            {hash.startsWith("#history/") && !saved && (
              <p className="feedback warning">
                Этот расчёт не найден в истории данного аккаунта.
              </p>
            )}
            <History
              entries={historyState.entries}
              onNew={newCalculation}
              onOpen={(s) => navigate("#history/" + s.id)}
            />
          </>
        ) : options ? (
          <>
            <div className="forecast-page-heading">
              <h1>Прогноз грузоперевозок</h1>
              <p>Оцените стоимость доставки и объём перевозок на выбранный год.</p>
            </div>
            <div className="forecast-workspace">
              <ConditionsForm
                key={formVersion}
                initial={draft}
                options={options}
                onDraft={setDraft}
                onResult={completed}
              />
              <ForecastOverview draft={draft} />
            </div>
          </>
        ) : (
          <section className="empty-state perforated">
            <h1>
              {catalogError
                ? "Не удалось загрузить каталог"
                : "Загружаем условия перевозки…"}
            </h1>
            <p role={catalogError ? "alert" : "status"}>
              {catalogError || "Получаем доступные города и грузы."}
            </p>
            {catalogError && (
              <button
                className="primary"
                onClick={() => setCatalogAttempt((n) => n + 1)}
              >
                Повторить загрузку
              </button>
            )}
          </section>
        )}
      </main>
      <Assistant
        key={userId ?? "guest"}
        onResult={showAssistantResult}
        onRequireAuth={user ? undefined : () => navigate("#login")}
      />
    </div>
  );
}
function App() {
  const [user, setUser] = useState<User | null>(null);
  const [checking, setChecking] = useState(true);
  const [workspaceVersion, setWorkspaceVersion] = useState(0);
  const [initialError, setInitialError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    request<{ user: User }>("/auth/me", { signal: controller.signal })
      .then((r) => setUser(r.user))
      .catch((error) => {
        if (
          !controller.signal.aborted &&
          !(error instanceof ApiError && error.status === 401)
        )
          setInitialError(errorText(error));
      })
      .finally(() => {
        if (!controller.signal.aborted) setChecking(false);
      });
    const onExpired = () => {
      setUser(null);
      setWorkspaceVersion((version) => version + 1);
      setInitialError("Сессия истекла. Войдите снова, чтобы открыть историю.");
    };
    window.addEventListener("session-expired", onExpired);
    return () => {
      controller.abort();
      window.removeEventListener("session-expired", onExpired);
    };
  }, []);
  const authenticated = useCallback((u: User) => {
    setUser(u);
    setInitialError("");
  }, []);
  if (checking)
    return (
      <div className="page">
        <header>
          <Brand />
        </header>
        <main>
          <p role="status" className="loading-state">
            Проверяем сессию…
          </p>
        </main>
      </div>
    );
  return (
    <Workspace
      key={workspaceVersion}
      user={user}
      onUser={authenticated}
      authMessage={initialError}
      onLogout={() => {
        setUser(null);
        setWorkspaceVersion((version) => version + 1);
      }}
    />
  );
}
createRoot(document.getElementById("root")!).render(<App />);
