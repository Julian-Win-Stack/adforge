import { useEffect, useState } from "react";
import { Chat } from "./Chat";
import { SessionList } from "./SessionList";
import { listSessions, type Session } from "./api";

export function App() {
  const [sessions, setSessions] = useState<Session[]>([]);
  const [openId, setOpenId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

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

  return (
    <main style={{ maxWidth: 960, margin: "2rem auto", fontFamily: "system-ui, sans-serif" }}>
      <h1>AdForge</h1>
      {error && <p style={{ color: "crimson" }}>{error}</p>}
      <div style={{ display: "flex", gap: "2rem", alignItems: "flex-start" }}>
        <SessionList
          sessions={sessions}
          openId={openId}
          onOpen={setOpenId}
          onNew={() => setOpenId(null)}
          onRenamed={remember}
        />
        <Chat
          key={openId ?? "new"}
          sessionId={openId}
          onSent={(sent) => {
            remember(sent.session);
            setOpenId(sent.session.id);
          }}
        />
      </div>
    </main>
  );
}
