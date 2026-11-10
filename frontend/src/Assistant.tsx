import { useEffect, useRef, useState, type PointerEvent } from "react";
import { errorText, post } from "./api";
import { getCorridorReport, hasDashboard, type AssistantDraft, type AssistantReply, type AssistantSource, type CorridorReport, type DashboardCalculation, type PendingTool } from "./assistantApi";
import { AssistantData } from "./AssistantData";
import { num, snapshotRoute, totalVolume, type Snapshot } from "./model";

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

export function Assistant({ snapshot, onResult, onRequireAuth }: {
  snapshot: Snapshot | null;
  onResult: (result: DashboardCalculation | CorridorReport) => void;
  onRequireAuth?: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [message, setMessage] = useState("");
  const [turns, setTurns] = useState<{ message: string; response: AssistantReply }[]>([]);
  const [draft, setDraft] = useState<AssistantDraft | null>(null);
  const [pending, setPending] = useState<PendingTool | null>(null);
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
  useEffect(() => () => active.current?.abort(), []);
  useEffect(() => {
    if (!open) return;
    close.current?.focus();
    function escape(e: KeyboardEvent) {
      if (e.key === "Escape") {
        setOpen(false);
        button.current?.focus();
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
  }, [open]);
  useEffect(() => {
    if (!open || !content.current) return;
    content.current.scrollTop = busy || error
      ? content.current.scrollHeight
      : latestReply.current?.offsetTop ?? 0;
  }, [open, turns, busy, error]);

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
      const response = await post<AssistantReply>("/assistant/chat", { message: text, draft, pending }, controller.signal, 190000);
      if (controller.signal.aborted) return;
      setTurns((previous) => [...previous, { message: text, response }]);
      setDraft(response.draft);
      setPending(response.pending);
      setMessage("");
      if (response.kind === "calculation" && hasDashboard(response.calculation)) {
        onResult(response.calculation);
      } else {
        const report = getCorridorReport(response);
        if (report) onResult(report);
      }
    } catch (failure) {
      if (!controller.signal.aborted) setError(errorText(failure));
    } finally {
      active.current = null;
      if (!controller.signal.aborted) {
        setBusy(false);
        requestAnimationFrame(() => input.current?.focus({ preventScroll: true }));
      }
    }
  }

  return (
    <>
      {open && (
        <section
          id="assistant-panel"
          ref={panel}
          role="dialog"
          aria-labelledby="assistant-title"
          className="assistant-panel"
          style={position ? { left: position.x, top: position.y, right: "auto", bottom: "auto" } : undefined}
          onPointerMove={movePanel}
          onLostPointerCapture={(event) => {
            if (drag.current?.pointerId === event.pointerId) drag.current = null;
          }}
        >
          <div className="assistant-drag-handle left" aria-hidden="true" onPointerDown={startDrag} />
          <div className="assistant-drag-handle right" aria-hidden="true" onPointerDown={startDrag} />
          <div className="assistant-heading" onPointerDown={startDrag}>
            <h2 id="assistant-title">
              <svg className="chat-icon" viewBox="0 0 24 24" aria-hidden="true">
                <path d="M21 11.5c0 5-4 8.5-9 8.5-1.5 0-3-.3-4.2-.9L3 21l1.5-4.4A8 8 0 0 1 3 11.5C3 6.8 7 3 12 3s9 3.8 9 8.5Z" />
              </svg>{" "}
              ИИ-ассистент
            </h2>
            <button
              className="icon-button"
              ref={close}
              aria-label="Закрыть ассистента"
              onClick={() => {
                setOpen(false);
                button.current?.focus();
              }}
            >
              ×
            </button>
          </div>
          <div className="assistant-content" ref={content} role="region" aria-label="Переписка с ассистентом" aria-live="polite" tabIndex={0}>
            {!turns.length && (
              <>
                <span className="availability">Планирование перевозок</span>
                <h3>Помощник по перевозкам</h3>
                <p>Например, «Сравни, как доставить 10 т муки из Москвы в Брянск».</p>
                {snapshot && (
                  <div className="assistant-context">
                    <small>Текущий расчёт</small>
                    <b>{snapshotRoute(snapshot)}</b>
                    <span>{num(totalVolume(snapshot.result), 6)} т · {snapshot.result.shipments} отправок</span>
                  </div>
                )}
              </>
            )}
            {turns.map(({ message, response }, index) => (
              <article key={index}>
                <p className="assistant-context">{message}</p>
                <p className="assistant-reply" ref={index === turns.length - 1 ? latestReply : null}>{response.reply}</p>
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
            />
            <button
              type="submit"
              disabled={busy || !message.trim()}
              aria-label="Отправить сообщение"
            >
              →
            </button>
          </form>
        </section>
      )}
      <button
        ref={button}
        type="button"
        className="assistant"
        aria-expanded={open}
        aria-controls="assistant-panel"
        onClick={onRequireAuth ?? (() => setOpen(!open))}
      >
        <i aria-hidden="true" /> ИИ-ассистент
      </button>
    </>
  );
}
