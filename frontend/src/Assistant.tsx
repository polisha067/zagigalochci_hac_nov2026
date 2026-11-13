import { useEffect, useRef, useState, type PointerEvent } from "react";
import { ApiError, errorText, post, request } from "./api";
import { dashboardResult, type AssistantDialog, type AssistantReply, type AssistantSource, type DashboardCalculation, type DialogScenario } from "./assistantApi";
import { AssistantData } from "./AssistantData";

export function SourceLinks({ sources }: { sources: AssistantSource[] }) {
  if (!sources.length) return null;
  return (
    <ul aria-label="Источники">
      {sources.map((source, index) => (
        <li key={index}>
          {source.url ? (
            <a href={source.url} target="_blank" rel="noreferrer">{source.title}</a>
          ) : (
            <span>{source.title}{source.path ? ` · ${source.path}` : ""}</span>
          )}
          {source.section ? ` · ${source.section}` : ""}
          {source.date ? ` · ${source.date}` : ""}
        </li>
      ))}
    </ul>
  );
}

type PanelPosition = { x: number; y: number };

function fitPanel(x: number, y: number, { width, height }: DOMRect): PanelPosition {
  const { clientWidth, clientHeight } = document.documentElement;
  return {
    x: Math.max(0, Math.min(x, clientWidth - width)),
    y: Math.max(0, Math.min(y, clientHeight - height)),
  };
}

export function Assistant({ onResult, onRequireAuth, embedded = false }: {
  embedded?: boolean;
  onResult: (result: DashboardCalculation, settlementNames: Record<string, string>, candidateId?: string) => void;
  onRequireAuth?: () => void;
}) {
  const [open, setOpen] = useState(embedded);
  const [message, setMessage] = useState("");
  const [turns, setTurns] = useState<{ message: string; dialog: AssistantDialog; response: AssistantReply }[]>([]);
  const [known, setKnown] = useState<string[]>([]);
  const [savedScenarios, setSavedScenarios] = useState<DialogScenario[]>([]);
  const [savedNames, setSavedNames] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [position, setPosition] = useState<PanelPosition | null>(null);
  const button = useRef<HTMLButtonElement>(null);
  const close = useRef<HTMLButtonElement>(null);
  const input = useRef<HTMLTextAreaElement>(null);
  const panel = useRef<HTMLElement>(null);
  const content = useRef<HTMLDivElement>(null);
  const latestReply = useRef<HTMLParagraphElement>(null);
  const drag = useRef<{ pointerId: number; offsetX: number; offsetY: number } | null>(null);
  const active = useRef<AbortController | null>(null);
  const revision = useRef<number | null>(null);
  const retry = useRef<{ message: string; revision: number; turn_id: string } | null>(null);
  useEffect(() => () => active.current?.abort(), []);
  useEffect(() => {
    if (!open || embedded) return;
    close.current?.focus({ preventScroll: true });
    function escape(e: KeyboardEvent) {
      if (e.key === "Escape") {
        setOpen(false);
        button.current?.focus({ preventScroll: true });
      }
    }
    window.addEventListener("keydown", escape);

    function fitToViewport() {
      const bounds = panel.current?.getBoundingClientRect();
      if (bounds) setPosition((previous) => previous && fitPanel(previous.x, previous.y, bounds));
    }
    fitToViewport();
    window.addEventListener("resize", fitToViewport);
    return () => {
      drag.current = null;
      window.removeEventListener("keydown", escape);
      window.removeEventListener("resize", fitToViewport);
    };
  }, [open, embedded]);
  useEffect(() => {
    if (!open || !content.current) return;
    content.current.scrollTop = busy || error
      ? content.current.scrollHeight
      : latestReply.current?.offsetTop ?? 0;
  }, [open, turns, busy, error]);

  useEffect(() => {
    if (!open || onRequireAuth || revision.current !== null) return;
    const controller = new AbortController();
    request<AssistantDialog>("/assistant/dialog", { signal: controller.signal }).then((dialog) => {
      if (controller.signal.aborted || revision.current !== null) return;
      revision.current = dialog.revision;
      setKnown(dialog.known);
      setSavedScenarios(dialog.scenarios);
      setSavedNames(dialog.settlement_names);
    }).catch(() => { /* Отправка повторно загрузит состояние и покажет ошибку при сбое. */ });
    return () => controller.abort();
  }, [open, onRequireAuth]);

  function startDrag(event: PointerEvent<HTMLDivElement>) {
    if (event.button !== 0 || drag.current || (event.target as Element).closest("button")) return;
    const bounds = panel.current?.getBoundingClientRect();
    if (!bounds) return;
    drag.current = {
      pointerId: event.pointerId,
      offsetX: event.clientX - bounds.left,
      offsetY: event.clientY - bounds.top,
    };
    event.currentTarget.setPointerCapture(event.pointerId);
  }

  function movePanel(event: PointerEvent<HTMLElement>) {
    const current = drag.current;
    if (current?.pointerId !== event.pointerId) return;
    setPosition(fitPanel(
      event.clientX - current.offsetX,
      event.clientY - current.offsetY,
      event.currentTarget.getBoundingClientRect(),
    ));
  }

  async function send() {
    if (onRequireAuth) { onRequireAuth(); return; }
    const text = message.trim();
    if (!text || active.current) return;
    const controller = new AbortController();
    active.current = controller;
    setBusy(true);
    setError("");
    try {
      if (revision.current === null) {
        const current = await request<AssistantDialog>("/assistant/dialog", { signal: controller.signal });
        revision.current = current.revision;
        setKnown(current.known);
      }
      if (retry.current?.message !== text) {
        retry.current = { message: text, revision: revision.current, turn_id: crypto.randomUUID() };
      }
      const dialog = await post<AssistantDialog>("/assistant/dialog", retry.current, controller.signal, 370000);
      const answer = dialog.answer;
      if (controller.signal.aborted || !answer) return;
      revision.current = dialog.revision;
      retry.current = null;
      setKnown(dialog.known);
      setSavedScenarios([]);
      setTurns((previous) => [...previous, { message: text, dialog, response: answer }]);
      setMessage("");
      const scenario = dialog.scenarios.find(({ answer }) => dashboardResult(answer));
      if (scenario) showResult(scenario.answer, dialog.settlement_names);
      else showResult(answer, dialog.settlement_names, dialog.selected_candidate ?? undefined);
    } catch (failure) {
      if (!controller.signal.aborted) setError(errorText(failure));
      if (failure instanceof ApiError && failure.code === "assistant_revision_conflict") {
        retry.current = null;
        revision.current = null;
        const latest = await request<AssistantDialog>("/assistant/dialog", { signal: controller.signal }).catch(() => null);
        if (latest && !controller.signal.aborted) {
          revision.current = latest.revision;
          setKnown(latest.known);
          setSavedScenarios(latest.scenarios);
          setSavedNames(latest.settlement_names);
        }
      }
    } finally {
      active.current = null;
      if (!controller.signal.aborted) {
        setBusy(false);
        requestAnimationFrame(() => input.current?.focus({ preventScroll: true }));
      }
    }
  }

  function showResult(response: AssistantReply, settlementNames: Record<string, string>, candidateId?: string) {
    const result = dashboardResult(response);
    if (result) onResult(result, settlementNames, candidateId);
  }

  function scenarioCards(scenarios: DialogScenario[], settlementNames: Record<string, string>) {
    return scenarios.map(({ title, answer }, index) => (
      <div className="assistant-scenario" key={index}>
        <b>{title}</b>
        <p className="assistant-reply">{answer.reply}</p>
        <AssistantData response={answer} />
        {dashboardResult(answer) && (
          <button type="button" className="text-button" onClick={() => showResult(answer, settlementNames)}>Открыть на карте</button>
        )}
        <SourceLinks sources={answer.sources} />
      </div>
    ));
  }

  return (
    <>
      {open && (
        <section
          id="assistant-panel"
          ref={panel}
          role={embedded ? "region" : "dialog"}
          aria-labelledby="assistant-title"
          className={`assistant-panel${embedded ? " assistant-embedded" : ""}`}
          style={!embedded && position ? { left: position.x, top: position.y, right: "auto", bottom: "auto" } : undefined}
          onPointerMove={embedded ? undefined : movePanel}
          onLostPointerCapture={(event) => {
            if (drag.current?.pointerId === event.pointerId) drag.current = null;
          }}
        >
          {!embedded && <>
            <div className="assistant-drag-handle left" aria-hidden="true" onPointerDown={startDrag} />
            <div className="assistant-drag-handle right" aria-hidden="true" onPointerDown={startDrag} />
          </>}
          <div className="assistant-heading" onPointerDown={embedded ? undefined : startDrag}>
            <h2 id="assistant-title">
              <svg className="chat-icon" viewBox="0 0 24 24" aria-hidden="true">
                <path d="M21 11.5c0 5-4 8.5-9 8.5-1.5 0-3-.3-4.2-.9L3 21l1.5-4.4A8 8 0 0 1 3 11.5C3 6.8 7 3 12 3s9 3.8 9 8.5Z" />
              </svg>{" "}
              {embedded ? "ИИ-агент" : "ИИ-ассистент"}
            </h2>
            {!embedded && <button
              className="icon-button"
              ref={close}
              aria-label="Закрыть ассистента"
              onClick={() => {
                setOpen(false);
                button.current?.focus({ preventScroll: true });
              }}
            >
              ×
            </button>}
          </div>
          <div className="assistant-content" ref={content} role="region" aria-label="Переписка с ассистентом" aria-live="polite" tabIndex={0}>
            {!turns.length && (
              <>
                <span className="availability">Планирование перевозок</span>
                <h3>Опишите перевозку своими словами</h3>
                <p>Агент уточнит условия, запустит расчёт и откроет прогноз с вариантами доставки.</p>
                <p>Например, «Сколько стоит перевезти 10 т муки из Москвы в Нижний Новгород через Владимир в 2030 году?»</p>
              </>
            )}
            {onRequireAuth && <div className="assistant-signin">
              <p>Для диалога с агентом войдите в аккаунт. Формой слева можно пользоваться без регистрации.</p>
              <button type="button" className="outline-button" onClick={onRequireAuth}>Войти и начать диалог</button>
            </div>}
            {known.length > 0 && (
              <details className="assistant-sources">
                <summary>Текущие условия</summary>
                <ul>{known.map((condition, index) => <li key={index}>{condition}</li>)}</ul>
              </details>
            )}
            {scenarioCards(savedScenarios, savedNames)}
            {turns.map(({ message, dialog, response }, index) => (
              <article key={index}>
                <p className="assistant-context">{message}</p>
                <p className="assistant-reply" ref={index === turns.length - 1 ? latestReply : null}>{response.reply}</p>
                {dialog.changes.length > 0 && <p>Обновлено — {dialog.changes.join(" · ")}</p>}
                {response.kind === "clarification" && dialog.known.length > 0 && <p>Уже учёл — {dialog.known.join(" · ")}</p>}
                {scenarioCards(dialog.scenarios, dialog.settlement_names)}
                <AssistantData response={response} />
                {response.sources.length > 0 && (
                  <details className="assistant-sources">
                    <summary>Источники</summary>
                    <SourceLinks sources={response.sources} />
                  </details>
                )}
              </article>
            ))}
            {busy && <p role="status">Обрабатываем запрос…</p>}
            {error && <p role="alert" className="feedback error">{error}</p>}
          </div>
          <form className="assistant-compose" onSubmit={(event) => { event.preventDefault(); void send(); }}>
            <label htmlFor="assistant-message" className="sr-only">
              Черновик сообщения ассистенту
            </label>
            <textarea
              id="assistant-message"
              ref={input}
              placeholder="Спросите о перевозке…"
              value={message}
              onChange={(e) => setMessage(e.target.value)}
              onKeyDown={(event) => {
                if (event.key !== "Enter" || event.shiftKey || event.nativeEvent.isComposing) return;
                event.preventDefault();
                event.currentTarget.form?.requestSubmit();
              }}
              maxLength={2000}
              readOnly={busy}
              disabled={!!onRequireAuth}
            />
            <button
              type="submit"
              disabled={!!onRequireAuth || busy || !message.trim()}
              aria-label="Отправить сообщение"
            >
              →
            </button>
          </form>
        </section>
      )}
      {!embedded && <button
        ref={button}
        type="button"
        className="assistant"
        aria-expanded={open}
        aria-controls="assistant-panel"
        onClick={onRequireAuth ?? (() => setOpen(!open))}
      >
        <i aria-hidden="true" /> ИИ-ассистент
      </button>}
    </>
  );
}
