import { Fragment, useEffect, useRef, useState } from "react";
import { errorText, post } from "./api";
import { hasDashboard, type AssistantDraft, type AssistantReply, type AssistantSource, type DashboardCalculation, type PendingTool } from "./assistantApi";
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

export function ReplyText({ text }: { text: string }) {
  return <p>{text.split("\n").map((line, index) => (
    <Fragment key={index}>{index > 0 && <br />}{line}</Fragment>
  ))}</p>;
}

export function Assistant({ snapshot, onResult }: {
  snapshot: Snapshot | null;
  onResult: (result: DashboardCalculation) => void;
}) {
  const [open, setOpen] = useState(false);
  const [message, setMessage] = useState("");
  const [turns, setTurns] = useState<{ message: string; response: AssistantReply }[]>([]);
  const [draft, setDraft] = useState<AssistantDraft | null>(null);
  const [pending, setPending] = useState<PendingTool | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const button = useRef<HTMLButtonElement>(null);
  const close = useRef<HTMLButtonElement>(null);
  const input = useRef<HTMLTextAreaElement>(null);
  const content = useRef<HTMLDivElement>(null);
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
    return () => window.removeEventListener("keydown", escape);
  }, [open]);
  useEffect(() => {
    if (open && content.current) content.current.scrollTop = content.current.scrollHeight;
  }, [open, turns, busy, error]);

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
      if (response.kind === "calculation" && hasDashboard(response.calculation)) onResult(response.calculation);
    } catch (failure) {
      if (!controller.signal.aborted) setError(errorText(failure));
    } finally {
      active.current = null;
      if (!controller.signal.aborted) {
        setBusy(false);
        requestAnimationFrame(() => input.current?.focus());
      }
    }
  }

  return (
    <>
      {open && (
        <section
          id="assistant-panel"
          role="dialog"
          aria-labelledby="assistant-title"
          className="assistant-panel"
        >
          <div className="assistant-heading">
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
          <div className="assistant-content" ref={content} aria-live="polite">
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
                <ReplyText text={response.reply} />
                <AssistantData response={response} />
                <SourceLinks sources={response.sources} />
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
            <small>Для начала с чистого черновика напишите «Новый расчёт».</small>
          </form>
        </section>
      )}
      <button
        ref={button}
        type="button"
        className="assistant"
        aria-expanded={open}
        aria-controls="assistant-panel"
        onClick={() => setOpen(!open)}
      >
        <i aria-hidden="true" /> ИИ-ассистент{" "}
        <span aria-hidden="true">{open ? "⌄" : ""}</span>
      </button>
    </>
  );
}
