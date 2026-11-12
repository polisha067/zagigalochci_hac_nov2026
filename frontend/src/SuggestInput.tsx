import { createPortal } from "react-dom";
import { useCallback } from "react";
import { useSuggestionMenu } from "./useSuggestionMenu";
import { useId, useState, type InputHTMLAttributes } from "react";

/** Свободный ввод с оформленными подсказками вместо системного datalist. */
export function SuggestInput({
  options,
  value,
  onChange,
  ...props
}: Omit<InputHTMLAttributes<HTMLInputElement>, "onChange" | "value"> & {
  options: { value: string; label: string }[];
  value: string;
  onChange: (value: string) => void;
}) {
  const id = useId();
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(-1);
  const matches = options.filter((o) =>
    `${o.value} ${o.label}`
      .toLocaleLowerCase()
      .includes(value.toLocaleLowerCase()),
  );
  const closeMenu = useCallback(() => setOpen(false), []);
  const { anchor, menu, position } = useSuggestionMenu(open, active, closeMenu);
  function choose(index: number) {
    if (!matches[index]) return;
    onChange(matches[index].value);
    setOpen(false);
    setActive(-1);
  }
  return (
    <span className="suggest-input">
      <input
        ref={anchor}
        {...props}
        value={value}
        autoComplete="off"
        role="combobox"
        aria-autocomplete="list"
        aria-expanded={open && matches.length > 0}
        aria-controls={`${id}-list`}
        aria-activedescendant={
          open && active >= 0 ? `${id}-${active}` : undefined
        }
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        onChange={(e) => {
          onChange(e.target.value);
          setActive(-1);
          setOpen(true);
        }}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown") {
            e.preventDefault();
            setOpen(true);
            setActive((i) => Math.min(i + 1, matches.length - 1));
          } else if (e.key === "ArrowUp") {
            e.preventDefault();
            setActive((i) => Math.max(0, i - 1));
          } else if (e.key === "Enter" && open && active >= 0) {
            e.preventDefault();
            choose(active);
          } else if (e.key === "Escape") {
            e.preventDefault();
            setOpen(false);
          }
        }}
      />
      {open && matches.length > 0 && createPortal(
        <span ref={menu} style={position} id={`${id}-list`} role="listbox" className="suggest-menu anchored-menu">
          {matches.map((option, index) => (
            <span
              role="option"
              key={option.value}
              id={`${id}-${index}`}
              aria-selected={index === active}
              className={`select-option${index === active ? " highlighted" : ""}`}
              onPointerDown={(e) => e.preventDefault()}
              onMouseEnter={() => setActive(index)}
              onClick={() => choose(index)}
            >
              {option.label}
            </span>
          ))}
        </span>, document.body
      )}
    </span>
  );
}
