import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { ApiError, copySession, type Session } from "./api";
import { CopyIcon } from "./icons";

/** The Make a copy button at the top of a chat or in its session row. It asks first, saying what the copy
 * gets and that the original stays as it is, shows that it is copying, and hands over the
 * copy to open. A refusal, as while the ad is still being made, is shown in the server's
 * words and changes nothing. */
export function CopyButton({
  sessionId,
  onCopied,
  iconOnly = false,
}: {
  sessionId: string;
  onCopied: (copy: Session) => void;
  /** The session list uses the same question with a compact row action. */
  iconOnly?: boolean;
}) {
  const [asking, setAsking] = useState(false);
  const [copying, setCopying] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const box = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const question = useRef<HTMLDivElement>(null);
  const confirm = useRef<HTMLButtonElement>(null);
  const [place, setPlace] = useState<{ top: number; left: number } | null>(null);

  // A row's question floats over the scrolling sidebar and stays inside the window.
  useLayoutEffect(() => {
    if (!asking || !iconOnly) return;
    function position() {
      if (!trigger.current || !question.current) return;
      const at = trigger.current.getBoundingClientRect();
      const { offsetWidth: width, offsetHeight: height } = question.current;
      const below = at.bottom + 8;
      setPlace({
        top: below + height > window.innerHeight - 8 ? Math.max(8, at.top - height - 8) : below,
        left: Math.max(8, Math.min(at.right - width, window.innerWidth - width - 8)),
      });
    }
    position();
    window.addEventListener("resize", position);
    window.addEventListener("scroll", position, true);
    return () => {
      window.removeEventListener("resize", position);
      window.removeEventListener("scroll", position, true);
    };
  }, [asking, iconOnly, error]);

  // Escape or a click outside puts the question away, unless the copy is being made.
  useEffect(() => {
    if (!asking) return;
    confirm.current?.focus();
    function away(event: MouseEvent) {
      const target = event.target as Node;
      if (!copying && !box.current?.contains(target) && !question.current?.contains(target))
        close();
    }
    function escape(event: globalThis.KeyboardEvent) {
      if (event.key === "Escape" && !copying) {
        close();
        trigger.current?.focus();
      }
    }
    document.addEventListener("mousedown", away);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("mousedown", away);
      document.removeEventListener("keydown", escape);
    };
  }, [asking, copying]);

  function close() {
    setAsking(false);
    setError(null);
  }

  async function copy() {
    if (copying) return;
    setCopying(true);
    setError(null);
    try {
      const made = await copySession(sessionId);
      setAsking(false);
      onCopied(made);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Couldn't reach the server. Try again.");
    } finally {
      setCopying(false);
    }
  }

  const dialog = asking && (
    <div
      className={`copy-ask${iconOnly ? " copy-ask-row" : ""}`}
      role="dialog"
      aria-label="Make a copy"
      ref={question}
      style={iconOnly ? (place ?? { visibility: "hidden" }) : undefined}
    >
      <p className="copy-ask-title">Make a copy of this chat?</p>
      <p className="copy-ask-text">
        The copy gets the whole chat, the scenes and every clip. Notes you send in the copy never
        change this chat.
      </p>
      {error && (
        <p role="alert" className="copy-ask-error">
          {error}
        </p>
      )}
      <div className="copy-ask-actions">
        <button
          className="copy-cancel"
          onClick={() => {
            close();
            trigger.current?.focus();
          }}
          disabled={copying}
        >
          Cancel
        </button>
        <button className="copy-confirm" onClick={copy} disabled={copying} ref={confirm}>
          {copying ? (
            <>
              <span className="spinner" aria-hidden="true" />
              Copying…
            </>
          ) : (
            "Make a copy"
          )}
        </button>
      </div>
    </div>
  );

  return (
    <div className={`copy${iconOnly ? " copy-row" : ""}`} ref={box}>
      <button
        className={iconOnly ? "icon-button" : "copy-button"}
        aria-label="Make a copy"
        title={iconOnly ? "Make a copy" : undefined}
        onClick={() => (asking ? close() : setAsking(true))}
        aria-expanded={asking}
        aria-haspopup="dialog"
        disabled={copying}
        ref={trigger}
      >
        <CopyIcon />
        {!iconOnly && <span>Make a copy</span>}
      </button>
      {iconOnly ? createPortal(dialog, document.body) : dialog}
    </div>
  );
}
