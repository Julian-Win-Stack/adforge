import { createPortal } from "react-dom";
import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";

/** One choice in a menu. A `checked` one is a pick among several, as the folder a session
 * is in; the rest are plain actions. */
export type MenuChoice =
  | { kind: "item"; label: string; onPick: () => void; icon?: ReactNode; danger?: boolean }
  | { kind: "radio"; label: string; checked: boolean; onPick: () => void; icon?: ReactNode }
  | { kind: "separator" }
  | { kind: "heading"; label: string };

/** A small menu that opens under the button that opened it, or above it where the window
 * runs out. It sits over the page, so a scrolling sidebar never cuts it off. Picking a
 * choice, Escape, a click elsewhere or scrolling closes it; Escape gives focus back. */
export function Menu({
  anchor,
  label,
  choices,
  onClose,
}: {
  anchor: HTMLElement;
  label: string;
  choices: MenuChoice[];
  onClose: () => void;
}) {
  const menu = useRef<HTMLDivElement>(null);
  const [place, setPlace] = useState<{ top: number; left: number } | null>(null);

  useLayoutEffect(() => {
    const at = anchor.getBoundingClientRect();
    const height = menu.current?.offsetHeight ?? 0;
    const width = menu.current?.offsetWidth ?? 0;
    const below = at.bottom + 4;
    const top = below + height > window.innerHeight - 8 ? Math.max(8, at.top - height - 4) : below;
    const left = Math.max(8, Math.min(at.right - width, window.innerWidth - width - 8));
    setPlace({ top, left });
  }, [anchor]);

  // Once it shows, since a hidden menu can't take focus.
  const shown = place !== null;
  useEffect(() => {
    if (shown) menu.current?.querySelector<HTMLElement>("[role^=menuitem]")?.focus();
  }, [shown]);

  useEffect(() => {
    function away(event: MouseEvent) {
      const target = event.target as Node;
      if (!menu.current?.contains(target) && !anchor.contains(target)) onClose();
    }
    // Only a scroll that moves the button closes it: the chat scrolling as messages
    // arrive leaves the button, and so the menu, where they were.
    function scrolled(event: Event) {
      const scroller = event.target;
      if (scroller instanceof Node && scroller.contains(anchor)) onClose();
    }
    document.addEventListener("mousedown", away);
    window.addEventListener("scroll", scrolled, true);
    window.addEventListener("resize", onClose);
    return () => {
      document.removeEventListener("mousedown", away);
      window.removeEventListener("scroll", scrolled, true);
      window.removeEventListener("resize", onClose);
    };
  }, [anchor, onClose]);

  function keys(event: React.KeyboardEvent) {
    if (event.key === "Escape") {
      event.preventDefault();
      onClose();
      anchor.focus();
      return;
    }
    if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
    event.preventDefault();
    const items = [...(menu.current?.querySelectorAll<HTMLElement>("[role^=menuitem]") ?? [])];
    const at = items.indexOf(document.activeElement as HTMLElement);
    const step = event.key === "ArrowDown" ? 1 : -1;
    items[(at + step + items.length) % items.length]?.focus();
  }

  return createPortal(
    <div
      ref={menu}
      role="menu"
      aria-label={label}
      className="menu"
      style={place ? { top: place.top, left: place.left } : { visibility: "hidden" }}
      onKeyDown={keys}
    >
      {choices.map((choice, i) => {
        if (choice.kind === "separator") return <div key={i} role="separator" />;
        if (choice.kind === "heading") {
          return (
            <div key={i} className="menu-heading" aria-hidden>
              {choice.label}
            </div>
          );
        }
        const pick = () => {
          onClose();
          choice.onPick();
        };
        return (
          <button
            key={i}
            type="button"
            tabIndex={-1}
            className={`menu-item${choice.kind === "item" && choice.danger ? " danger" : ""}`}
            {...(choice.kind === "radio"
              ? { role: "menuitemradio", "aria-checked": choice.checked }
              : { role: "menuitem" })}
            onClick={pick}
          >
            <span className="menu-icon">{choice.icon}</span>
            <span className="menu-label">{choice.label}</span>
            {choice.kind === "radio" && choice.checked && (
              <span className="menu-check" aria-hidden>
                ✓
              </span>
            )}
          </button>
        );
      })}
    </div>,
    document.body,
  );
}
