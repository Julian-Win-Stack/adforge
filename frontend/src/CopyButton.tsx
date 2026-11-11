import { useEffect, useRef, useState } from "react";
import { ApiError, copySession, type Session } from "./api";
import { CopyIcon } from "./icons";

/** The Make a copy button at the top of a chat (#566). It asks first, saying what the copy
 * gets and that the original stays as it is, shows that it is copying, and hands over the
 * copy to open. A refusal, as while the ad is still being made, is shown in the server's
 * words and changes nothing. */
export function CopyButton({
  sessionId,
  onCopied,
}: {
  sessionId: string;
  onCopied: (copy: Session) => void;
}) {
  const [asking, setAsking] = useState(false);
  const [copying, setCopying] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const box = useRef<HTMLDivElement>(null);
  const confirm = useRef<HTMLButtonElement>(null);

  // Escape or a click outside puts the question away, unless the copy is being made.
  useEffect(() => {
    if (!asking) return;
    confirm.current?.focus();
    function away(event: MouseEvent) {
      if (!copying && box.current && !box.current.contains(event.target as Node)) close();
    }
    function escape(event: globalThis.KeyboardEvent) {
      if (event.key === "Escape" && !copying) close();
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

  return (
    <div className="copy" ref={box}>
      <button
        className="copy-button"
        onClick={() => (asking ? close() : setAsking(true))}
        aria-expanded={asking}
        aria-haspopup="dialog"
      >
        <CopyIcon />
        <span>Make a copy</span>
      </button>
      {asking && (
        <div className="copy-ask" role="dialog" aria-label="Make a copy">
          <p className="copy-ask-title">Make a copy of this chat?</p>
          <p className="copy-ask-text">
            The copy gets the whole chat, the scenes and every clip. Notes you send in the copy
            never change this chat.
          </p>
          {error && (
            <p role="alert" className="copy-ask-error">
              {error}
            </p>
          )}
          <div className="copy-ask-actions">
            <button className="copy-cancel" onClick={close} disabled={copying}>
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
      )}
    </div>
  );
}
