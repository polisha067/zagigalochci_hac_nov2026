import { useEffect, useRef, useState } from "react";
import { num, snapshotRoute, totalVolume, type Snapshot } from "./model";
export function Assistant({ snapshot }: { snapshot: Snapshot | null }) {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState("");
  const button = useRef<HTMLButtonElement>(null);
  const close = useRef<HTMLButtonElement>(null);
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
          <div className="assistant-content">
            <span className="availability">Подключение ожидается</span>
            <h3>Помощник по перевозкам</h3>
            <p>
              Сервис ИИ пока не подключён. Отправка сообщений станет доступна
              после подключения — сейчас ответы не генерируются.
            </p>
            {snapshot && (
              <div className="assistant-context">
                <small>Текущий расчёт</small>
                <b>{snapshotRoute(snapshot)}</b>
                <span>
                  {num(totalVolume(snapshot.result), 6)} т ·{" "}
                  {snapshot.result.shipments} отправок
                </span>
              </div>
            )}
            <p className="muted">
              Расчёт стоимости и подробные отчёты уже доступны без ассистента.
            </p>
          </div>
          <div className="assistant-compose">
            <label htmlFor="assistant-message" className="sr-only">
              Черновик сообщения ассистенту
            </label>
            <textarea
              id="assistant-message"
              placeholder="Спросите о перевозке…"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              maxLength={4000}
            />
            <button
              disabled
              aria-label="Отправка недоступна: сервис ИИ не подключён"
            >
              →
            </button>
            <small>
              Можно подготовить черновик. Он никуда не отправляется.
            </small>
          </div>
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
