import { useEffect, useState } from "react";
import { Chat } from "./Chat";
import { SessionList } from "./SessionList";
import { listSessions, type Session } from "./api";

export function App() {
  const [sessions, setSessions] = useState<Session[]>([]);
  const [openId, setOpenId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [sidebarOpen, setSidebarOpen] = useState(() => !isNarrow());

  useEffect(() => {
    listSessions()
      .then(setSessions)
      .catch(() => setError("Couldn't load your sessions."));
  }, []);

  /** Keeps the sidebar in step: a renamed session stays where it is, a new one goes on top. */
  function remember(session: Session) {
    setSessions((all) =>
      all.some((one) => one.id === session.id)
        ? all.map((one) => (one.id === session.id ? session : one))
        : [session, ...all],
    );
  }

  const open = sessions.find((one) => one.id === openId) ?? null;

  /** On a phone the sidebar covers the chat, so picking a session puts it away. */
  function go(sessionId: string | null) {
    setOpenId(sessionId);
    if (isNarrow()) setSidebarOpen(false);
  }

  return (
    <div className={`app ${sidebarOpen ? "sidebar-open" : "sidebar-closed"}`}>
      {error && (
        <p role="alert" className="notice error app-error">
          {error}
        </p>
      )}
      <SessionList
        sessions={sessions}
        openId={openId}
        onOpen={go}
        onNew={() => go(null)}
        onRenamed={remember}
        onHide={() => setSidebarOpen(false)}
      />
      {sidebarOpen && <div className="backdrop" onClick={() => setSidebarOpen(false)} />}
      <Chat
        key={openId ?? "new"}
        sessionId={openId}
        title={open?.name ?? null}
        sidebarOpen={sidebarOpen}
        onShowSidebar={() => setSidebarOpen(true)}
        onSent={(sent) => {
          remember(sent.session);
          setOpenId(sent.session.id);
        }}
      />
    </div>
  );
}

/** A phone-sized window, where the sidebar can't sit beside the chat. */
function isNarrow() {
  return window.innerWidth <= 720;
}
