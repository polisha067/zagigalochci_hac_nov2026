import { createPortal } from "react-dom";
import { useCallback } from "react";
import { useSuggestionMenu } from "./useSuggestionMenu";
import { useEffect, useId, useState } from "react";
import { errorText, request } from "./api";
import type { Settlement, SettlementPage } from "./networkApi";
export function SettlementPicker({
  label,
  value,
  initial,
  onChange,
}: {
  label: string;
  value: Settlement | null;
  initial: Settlement[];
  onChange: (value: Settlement | null) => void;
}) {
  const id = useId();
  const [query, setQuery] = useState(value?.name || "");
  const [open, setOpen] = useState(false);
  const [items, setItems] = useState(initial);
  const [total, setTotal] = useState(initial.length);
  const [active, setActive] = useState(-1);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    if (value) setQuery(value.name);
  }, [value?.id, value?.name]);
  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    setLoading(true);
    setError("");
    setActive(-1);
    const timer = window.setTimeout(() => {
      request<SettlementPage>(
        `/network/russia/options?${new URLSearchParams({ query: query.trim(), limit: "50" })}`,
        { signal: controller.signal },
      )
        .then((data) => {
          setItems(data.settlements);
          setTotal(data.total);
        })
        .catch((e) => {
          if (!controller.signal.aborted) {
            setItems([]);
            setError(errorText(e));
          }
        })
        .finally(() => {
          if (!controller.signal.aborted) setLoading(false);
        });
    }, 200);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [query, open]);
  const closeMenu = useCallback(() => setOpen(false), []);
  const { anchor, menu, position } = useSuggestionMenu(open, active, closeMenu);
  function choose(item: Settlement) {
    onChange(item);
    setQuery(item.name);
    setOpen(false);
    setActive(-1);
  }
  return (
    <div className="settlement-picker">
      <label htmlFor={id}>{label}</label>
      <input
        ref={anchor}
        id={id}
        role="combobox"
        aria-label={label}
        autoComplete="off"
        aria-autocomplete="list"
        aria-expanded={open}
        aria-controls={id + "-list"}
        aria-activedescendant={
          open && active >= 0 ? `${id}-option-${active}` : undefined
        }
        placeholder="Начните вводить населённый пункт"
        value={query}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        onChange={(e) => {
          setQuery(e.target.value);
          if (value) onChange(null);
          setOpen(true);
        }}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown") {
            e.preventDefault();
            setOpen(true);
            setActive((a) => Math.min(a + 1, items.length - 1));
          }
          if (e.key === "ArrowUp") {
            e.preventDefault();
            setActive((a) => Math.max(a - 1, 0));
          }
          if (e.key === "Escape") {
            e.preventDefault();
            e.stopPropagation();
            setOpen(false);
          }
          if (
            e.key === "Enter" &&
            open &&
            active >= 0 &&
            items[active] &&
            !loading
          ) {
            e.preventDefault();
            choose(items[active]);
          }
        }}
      />
      {open && createPortal(
        <div
          ref={menu as React.RefObject<HTMLDivElement>}
          style={position}
          className="settlement-menu anchored-menu"
          id={id + "-list"}
          role="listbox"
          aria-label={`Населённые пункты: ${label}`}
        >
          {loading ? (
            <p role="status">Ищем…</p>
          ) : error ? (
            <p role="alert">{error}</p>
          ) : items.length ? (
            <>
              {items.map((item, i) => (
                <button
                  type="button"
                  role="option"
                  aria-selected={i === active}
                  key={item.id}
                  id={`${id}-option-${i}`}
                  onPointerDown={(e) => e.preventDefault()}
                  onClick={() => choose(item)}
                >
                  <b>{item.name}</b>
                  {item.region && <small>{item.region}</small>}
                </button>
              ))}
              {total > items.length && (
                <p>Найдено {total}. Уточните название, чтобы сузить поиск.</p>
              )}
            </>
          ) : (
            <p>Населённый пункт не найден в загруженном справочнике.</p>
          )}
        </div>, document.body
      )}
      {!open && query && !value && (
        <small className="selection-hint">
          Выберите населённый пункт из подсказок.
        </small>
      )}
    </div>
  );
}
