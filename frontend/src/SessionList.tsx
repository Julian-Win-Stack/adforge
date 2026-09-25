import { useState, type FormEvent } from "react";
import { ApiError, renameSession, type Session } from "./api";
import { PencilIcon, PlusIcon, SidebarIcon, SparkIcon } from "./icons";

/** The sessions on the side: every past one newest first, the one being read, and a
 * way to start another. A session has no name until its first message gives it one. */
export function SessionList({
  sessions,
  openId,
  onOpen,
  onNew,
  onRenamed,
  onHide,
}: {
  sessions: Session[];
  openId: string | null;
  onOpen: (sessionId: string) => void;
  onNew: () => void;
  onRenamed: (session: Session) => void;
  onHide: () => void;
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

  const groups = groupByDay(sessions);

  return (
    <nav aria-label="Sessions" className="sidebar">
      <div className="sidebar-inner">
        <div className="brand">
          <span className="brand-mark">
            <SparkIcon size={16} />
          </span>
          <span className="brand-name">AdForge</span>
          <span className="brand-spacer" />
          <button className="icon-button" onClick={onHide} aria-label="Hide sessions">
            <SidebarIcon />
          </button>
        </div>

        <button className="new-session" onClick={onNew}>
          <PlusIcon size={16} />
          New session
        </button>

        {error && (
          <p role="alert" className="notice error" style={{ marginTop: 10 }}>
            {error}
          </p>
        )}

        <div className="session-groups">
          {groups.map(([title, members]) => (
            <div key={title}>
              <div className="session-group-title">{title}</div>
              <ul className="session-list">
                {members.map((session) => (
                  <li
                    key={session.id}
                    className={`session-item${session.id === openId ? " active" : ""}`}
                  >
                    {renaming === session.id ? (
                      <form className="rename-form" onSubmit={save}>
                        <input
                          aria-label="Session name"
                          value={name}
                          onChange={(e) => setName(e.target.value)}
                          onKeyDown={(e) => {
                            if (e.key === "Escape") setRenaming(null);
                          }}
                          autoFocus
                        />
                        <button type="submit" className="text-button primary">
                          Save
                        </button>
                      </form>
                    ) : (
                      <>
                        <button
                          className="session-open"
                          onClick={() => onOpen(session.id)}
                          aria-current={session.id === openId}
                          title={session.name || "Untitled"}
                        >
                          {session.name || "Untitled"}
                        </button>
                        {session.id === openId && (
                          <button
                            className="icon-button"
                            aria-label="Rename"
                            title="Rename"
                            onClick={() => {
                              setRenaming(session.id);
                              setName(session.name);
                            }}
                          >
                            <PencilIcon />
                          </button>
                        )}
                      </>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>

        <div className="sidebar-foot">One producer. One box. One finished ad.</div>
      </div>
    </nav>
  );
}

/** Today's sessions, yesterday's, then everything older, each newest first as given. */
function groupByDay(sessions: Session[]): [string, Session[]][] {
  const today = new Date();
  const startOfToday = new Date(today.getFullYear(), today.getMonth(), today.getDate()).getTime();
  const startOfYesterday = startOfToday - 24 * 60 * 60 * 1000;
  const buckets: Record<string, Session[]> = { Today: [], Yesterday: [], Older: [] };
  for (const session of sessions) {
    const at = new Date(session.created_at).getTime();
    const key = at >= startOfToday ? "Today" : at >= startOfYesterday ? "Yesterday" : "Older";
    buckets[key].push(session);
  }
  return Object.entries(buckets).filter(([, members]) => members.length > 0);
}
