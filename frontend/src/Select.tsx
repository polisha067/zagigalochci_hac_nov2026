import {
  Children,
  isValidElement,
  useId,
  useLayoutEffect,
  useRef,
  useState,
  type ChangeEvent,
  type ReactNode,
  type SelectHTMLAttributes,
} from "react";
import { createPortal } from "react-dom";

type Option = { value: string; label: string; disabled: boolean };
function textOf(node: ReactNode): string {
  return Children.toArray(node)
    .map((child) =>
      isValidElement<{ children?: ReactNode }>(child)
        ? textOf(child.props.children)
        : String(child),
    )
    .join("");
}
function optionsOf(children: ReactNode): Option[] {
  return Children.toArray(children).flatMap((child) => {
    if (
      !isValidElement<{
        value?: string | number;
        children?: ReactNode;
        disabled?: boolean;
      }>(child)
    )
      return [];
    if (child.type !== "option") return optionsOf(child.props.children);
    const label = textOf(child.props.children);
    return [
      {
        value: String(child.props.value ?? label),
        label,
        disabled: !!child.props.disabled,
      },
    ];
  });
}

/** Единое меню выбора: клавиатура, поиск по первым буквам и раскрытие поверх карточек. */
export function Select({
  children,
  value,
  onChange,
  className = "",
  ...props
}: SelectHTMLAttributes<HTMLSelectElement>) {
  const options = optionsOf(children);
  const id = useId();
  const button = useRef<HTMLButtonElement>(null);
  const native = useRef<HTMLSelectElement>(null);
  const menu = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const [label, setLabel] = useState("");
  const [position, setPosition] = useState({
    top: 0,
    left: 0,
    width: 0,
    maxHeight: 280,
  });
  const search = useRef({ text: "", at: 0 });
  const selected = Math.max(
    0,
    options.findIndex((o) => o.value === String(value ?? "")),
  );
  useLayoutEffect(() => {
    const parent = button.current?.closest("label");
    if (parent) {
      const clone = parent.cloneNode(true) as HTMLElement;
      clone
        .querySelectorAll(".custom-select, input, select, button")
        .forEach((el) => el.remove());
      setLabel(clone.textContent?.trim() || "Выберите значение");
    }
  }, [children]);
  useLayoutEffect(() => {
    if (!open) return;
    const place = () => {
      const bounds = button.current!.getBoundingClientRect();
      const viewport = window.visualViewport;
      const bottom = (viewport?.height ?? window.innerHeight) + (viewport?.offsetTop ?? 0);
      const edge = (viewport?.offsetTop ?? 0) + 8;
      if (bounds.bottom < edge || bounds.top > bottom) { setOpen(false); return; }
      const below = bottom - bounds.bottom - 12;
      const above = bounds.top - edge - 6;
      const height = Math.min(280, options.length * 42 + 12);
      const up = below < height && above > below;
      const maxHeight = Math.max(0, Math.min(height, up ? above : below));
      const width = Math.min(bounds.width, window.innerWidth - 16);
      setPosition({
        top: up ? bounds.top - maxHeight - 6 : bounds.bottom + 6,
        left: Math.max(
          8,
          Math.min(bounds.left, window.innerWidth - width - 8),
        ),
        width,
        maxHeight,
      });
    };
    const outside = (event: PointerEvent) => {
      if (
        !button.current?.contains(event.target as Node) &&
        !menu.current?.contains(event.target as Node)
      )
        setOpen(false);
    };
    place();
    window.addEventListener("resize", place);
    window.visualViewport?.addEventListener("resize", place);
    window.addEventListener("scroll", place, true);
    document.addEventListener("pointerdown", outside);
    return () => {
      window.removeEventListener("resize", place);
      window.visualViewport?.removeEventListener("resize", place);
      window.removeEventListener("scroll", place, true);
      document.removeEventListener("pointerdown", outside);
    };
  }, [open, options.length]);
  useLayoutEffect(() => {
    const list = menu.current;
    const option = list?.querySelector<HTMLElement>(`[data-index="${active}"]`);
    if (!open || !list || !option) return;
    const top = option.offsetTop;
    if (top < list.scrollTop) list.scrollTop = top;
    else if (top + option.offsetHeight > list.scrollTop + list.clientHeight)
      list.scrollTop = top + option.offsetHeight - list.clientHeight;
  }, [active, open]);
  function choose(index: number) {
    const option = options[index];
    if (!option || option.disabled) return;
    if (native.current) {
      native.current.value = option.value;
      onChange?.({
        target: native.current,
        currentTarget: native.current,
      } as ChangeEvent<HTMLSelectElement>);
    }
    setOpen(false);
    button.current?.focus({ preventScroll: true });
  }
  function move(direction: number) {
    let index = open ? active : selected;
    for (let i = 0; i < options.length; i++) {
      index = (index + direction + options.length) % options.length;
      if (!options[index].disabled) break;
    }
    setActive(index);
    setOpen(true);
  }
  return (
    <span className={`custom-select ${className}`}>
      <select
        {...props}
        ref={native}
        value={value}
        onChange={onChange}
        hidden
        aria-hidden="true"
        tabIndex={-1}
      >
        {children}
      </select>
      <button
        ref={button}
        type="button"
        className="select-trigger"
        role="combobox"
        aria-label={props["aria-label"] || label || "Выберите значение"}
        aria-labelledby={props["aria-labelledby"]}
        aria-expanded={open}
        aria-controls={`${id}-list`}
        aria-haspopup="listbox"
        aria-activedescendant={open ? `${id}-${active}` : undefined}
        disabled={props.disabled}
        aria-invalid={props["aria-invalid"]}
        aria-describedby={props["aria-describedby"]}
        onBlur={(event) => {
          if (!menu.current?.contains(event.relatedTarget)) setOpen(false);
        }}
        onClick={() => {
          setActive(selected);
          setOpen(!open);
        }}
        onKeyDown={(event) => {
          if (
            [
              "ArrowDown",
              "ArrowUp",
              "Home",
              "End",
              "Enter",
              " ",
              "Escape",
            ].includes(event.key)
          )
            event.preventDefault();
          if (event.key === "ArrowDown") move(1);
          else if (event.key === "ArrowUp") move(-1);
          else if (event.key === "Home") {
            setActive(options.findIndex((o) => !o.disabled));
            setOpen(true);
          } else if (event.key === "End") {
            setActive(
              options.length -
                1 -
                [...options].reverse().findIndex((o) => !o.disabled),
            );
            setOpen(true);
          } else if (event.key === "Enter" || event.key === " ") {
            if (open) choose(active);
            else {
              setActive(selected);
              setOpen(true);
            }
          } else if (event.key === "Escape" || event.key === "Tab")
            setOpen(false);
          else if (event.key.length === 1 && !event.ctrlKey && !event.metaKey) {
            const now = Date.now();
            search.current.text =
              (now - search.current.at < 700 ? search.current.text : "") +
              event.key;
            search.current.at = now;
            const index = options.findIndex(
              (o) =>
                !o.disabled &&
                o.label
                  .toLocaleLowerCase()
                  .startsWith(search.current.text.toLocaleLowerCase()),
            );
            if (index >= 0) {
              setActive(index);
              setOpen(true);
            }
          }
        }}
      >
        <span className="select-value">
          {options[selected]?.label || "Выберите значение"}
        </span>
        <svg viewBox="0 0 20 20" width="18" height="18" aria-hidden="true">
          <path
            d="m5 7.5 5 5 5-5"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.7"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      </button>
      {open &&
        createPortal(
          <div
            ref={menu}
            id={`${id}-list`}
            role="listbox"
            aria-label={props["aria-label"] || label}
            className="select-menu"
            style={position}
            onPointerDown={(event) => event.preventDefault()}
          >
            {options.map((option, index) => (
              <div
                key={option.value}
                id={`${id}-${index}`}
                data-index={index}
                role="option"
                aria-selected={index === selected}
                aria-disabled={option.disabled}
                className={`select-option${index === active ? " highlighted" : ""}`}
                onPointerMove={() => {
                  if (!option.disabled) setActive(index);
                }}
                onClick={() => choose(index)}
              >
                <span>{option.label}</span>
                {index === selected && <span aria-hidden="true">✓</span>}
              </div>
            ))}
          </div>,
          document.body,
        )}
    </span>
  );
}
