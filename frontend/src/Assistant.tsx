import { useEffect, useLayoutEffect, useRef, useState, type PointerEvent } from "react";
import { createPortal } from "react-dom";
import { ApiError, errorText, post, request } from "./api";
import { assistantVolumeSelection, dashboardResult, type VolumeForecastSelection, type AssistantDialog, type AssistantReply, type AssistantSource, type DashboardCalculation, type DialogScenario } from "./assistantApi";
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

export function Assistant({ onResult, onVolumeResult, onSaveResult, hidden, embeddedHost }: {
  onResult: (result: DashboardCalculation, settlementNames: Record<string, string>, candidateId?: string) => void;
  onVolumeResult: (selection: VolumeForecastSelection) => void;
  onSaveResult?: (result: DashboardCalculation, settlementNames: Record<string, string>) => void;
  hidden?: boolean;
  embeddedHost?: HTMLElement | null;
}) {
  const embedded = !!embeddedHost;
  const [floatingOpen, setOpen] = useState(false);
  const open = embedded || floatingOpen;
  const [message, setMessage] = useState("");
  const [turns, setTurns] = useState<{ message: string; dialog: AssistantDialog; response: AssistantReply }[]>([]);
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
  const loadingDialog = useRef<Promise<AssistantDialog> | null>(null);
  const retry = useRef<{ message: string; revision: number; turn_id: string } | null>(null);
  useLayoutEffect(() => {
    const field = input.current;
    if (!open || hidden || !field) return;

    function fitDraft() {
      if (!field) return;
      field.style.height = "auto";
      field.style.height = `${field.scrollHeight}px`;
    }

    fitDraft();
    let width = field.clientWidth;
    const observer = new ResizeObserver(() => {
      if (width === field.clientWidth) return;
      width = field.clientWidth;
      fitDraft();
    });
    observer.observe(field);
    return () => observer.disconnect();
  }, [message, open, hidden, embedded]);

  useEffect(() => () => active.current?.abort(), []);
  useEffect(() => {
    if (!open || hidden || embedded) return;
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
  }, [open, hidden, embedded]);
  useEffect(() => {
    if (!open || !content.current) return;
    content.current.scrollTop = busy || error
      ? content.current.scrollHeight
      : latestReply.current?.offsetTop ?? 0;
  }, [open, turns, busy, error]);

  useEffect(() => {
    if (!open || revision.current !== null) return;
    const controller = new AbortController();
    const loading = request<AssistantDialog>("/assistant/dialog", { signal: controller.signal });
    loadingDialog.current = loading;
    loading.then((dialog) => {
      if (controller.signal.aborted || revision.current !== null) return;
      revision.current = dialog.revision;
      setSavedScenarios(dialog.scenarios);
      setSavedNames(dialog.settlement_names);
    }).catch(() => { /* Отправка повторно загрузит состояние и покажет ошибку при сбое. */ });
    return () => {
      controller.abort();
      if (loadingDialog.current === loading) loadingDialog.current = null;
    };
  }, [open]);

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
    const text = message.trim();
    if (!text || active.current) return;
    const controller = new AbortController();
    active.current = controller;
    setBusy(true);
    setError("");
    try {
      if (revision.current === null) {
        const current = await (loadingDialog.current ?? request<AssistantDialog>("/assistant/dialog", { signal: controller.signal }));
        revision.current = current.revision;
      }
      if (retry.current?.message !== text) {
        retry.current = { message: text, revision: revision.current, turn_id: crypto.randomUUID() };
      }
      const dialog = await post<AssistantDialog>("/assistant/dialog", retry.current, controller.signal, 370000);
      const answer = dialog.answer;
      if (controller.signal.aborted || !answer) return;
      revision.current = dialog.revision;
      retry.current = null;
      setSavedScenarios([]);
      setTurns((previous) => [...previous, { message: text, dialog, response: answer }]);
      setMessage("");
      const volume = assistantVolumeSelection(answer);
      if (volume) {
        onVolumeResult(volume);
        return;
      }
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

  function savePrompt(answer: AssistantReply, settlementNames: Record<string, string>) {
    const result = dashboardResult(answer);
    if (!onSaveResult || !result) return null;
    return (
      <button type="button" className="text-button" onClick={() => onSaveResult(result, settlementNames)}>
        Войти и сохранить расчёт в историю
      </button>
    );
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
        {savePrompt(answer, settlementNames)}
        <SourceLinks sources={answer.sources} />
      </div>
    ));
  }

  const assistant = (
    <>
      {open && (
        <section
          id="assistant-panel"
          ref={panel}
          role={embedded ? "region" : "dialog"}
          aria-labelledby="assistant-title"
          className={`assistant-panel${embedded ? " assistant-embedded" : ""}${!turns.length ? " assistant-empty" : ""}`}
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
                <h3>Можно просто словами</h3>
                <p>Опишите перевозку — помогу собрать условия и сравнить варианты.</p>
                <div className="assistant-example-block">
                  <span>Например</span>
                  <button type="button" className="assistant-example" onClick={() => {
                    setMessage("Новый расчёт. Сколько стоит перевезти 100 тонн муки из Москвы во Владивосток в 2030 году? Отправка разовая, мука в коробках, объём 20 м³, партию можно разделить. Покажи варианты авто, ж/д и авиа.");
                    input.current?.focus({ preventScroll: true });
                  }}>Москва → Владивосток · 100 тонн муки</button>
                  <div className="assistant-example-tags"><span>2030 год</span><span>Авто · Ж/д · Авиа</span></div>
                </div>
              </>
            )}
            {scenarioCards(savedScenarios, savedNames)}
            {turns.map(({ message, dialog, response }, index) => (
              <article key={index}>
                <p className="assistant-context">{message}</p>
                <p className="assistant-reply" ref={index === turns.length - 1 ? latestReply : null}>{response.reply}</p>
                {dialog.changes.length > 0 && <p className="assistant-turn-note">Обновлено — {dialog.changes.join(" · ")}</p>}
                {response.kind === "clarification" && dialog.known.length > 0 && <p className="assistant-turn-note">Уже учёл — {dialog.known.join(" · ")}</p>}
                {scenarioCards(dialog.scenarios, dialog.settlement_names)}
                <AssistantData response={response} />
                {savePrompt(response, dialog.settlement_names)}
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
              rows={1}
              ref={input}
              placeholder="Расскажите о перевозке…"
              value={message}
              onChange={(e) => setMessage(e.target.value)}
              onKeyDown={(event) => {
                if (event.key !== "Enter" || event.shiftKey || event.nativeEvent.isComposing) return;
                event.preventDefault();
                event.currentTarget.form?.requestSubmit();
              }}
              maxLength={2000}
              readOnly={busy}
            />
            <button
              type="submit"
              disabled={busy || !message.trim()}
              aria-label="Отправить сообщение"
            >
              →
            </button>
          </form>
          {embedded && <p className="assistant-input-note">Агент уточнит недостающие детали</p>}
        </section>
      )}
      {!embedded && <button
        ref={button}
        type="button"
        className="assistant"
        aria-expanded={open}
        aria-controls="assistant-panel"
        onClick={() => setOpen(!open)}
      >
        <i aria-hidden="true" /> ИИ-ассистент
      </button>}
    </>
  );
  return embeddedHost ? createPortal(assistant, embeddedHost) : assistant;
}
