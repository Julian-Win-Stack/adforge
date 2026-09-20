import { useState, type FormEvent } from "react";
import { ApiError, renameSession, type Session } from "./api";

/** The sessions on the side: every past one newest first, the one being read, and a
 * way to start another. A session has no name until its first message gives it one. */
export function SessionList({
  sessions,
  openId,
  onOpen,
  onNew,
  onRenamed,
}: {
  sessions: Session[];
  openId: string | null;
  onOpen: (sessionId: string) => void;
  onNew: () => void;
  onRenamed: (session: Session) => void;
}) {
  const [renaming, setRenaming] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);

  async function save(event: FormEvent) {
    event.preventDefault();
    if (renaming === null) return;
    try {
      onRenamed(await renameSession(renaming, name));
      setRenaming(null);
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Couldn't reach the server. Try again.");
    }
  }

  return (
    <nav aria-label="Sessions" style={{ width: 220, flexShrink: 0 }}>
      <button onClick={onNew}>New session</button>
      {error && <p style={{ color: "crimson" }}>{error}</p>}
      <ul style={{ listStyle: "none", padding: 0 }}>
        {sessions.map((session) => (
          <li key={session.id} style={{ marginBottom: "0.5rem" }}>
            {renaming === session.id ? (
              <form onSubmit={save}>
                <label>
                  Session name
                  <input value={name} onChange={(e) => setName(e.target.value)} autoFocus />
                </label>
                <button type="submit">Save</button>
              </form>
            ) : (
              <>
                <button
                  onClick={() => onOpen(session.id)}
                  aria-current={session.id === openId}
                  style={{ fontWeight: session.id === openId ? "bold" : "normal" }}
                >
                  {session.name || "Untitled"}
                </button>
                {session.id === openId && (
                  <button
                    onClick={() => {
                      setRenaming(session.id);
                      setName(session.name);
                    }}
                  >
                    Rename
                  </button>
                )}
              </>
            )}
          </li>
        ))}
      </ul>
    </nav>
  );
}
