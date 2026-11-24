import { useEffect, useState, type CSSProperties } from "react";
import { Chat } from "./Chat";
import { SessionList, sessionTitle } from "./SessionList";
import { listFolders, listSessions, type Folder, type Session } from "./api";

const SIDEBAR_MIN = 200;
const SIDEBAR_MAX = 480;
const SIDEBAR_STEP = 16;

export function App() {
  const [sessions, setSessions] = useState<Session[]>([]);
  const [folders, setFolders] = useState<Folder[]>([]);
  const [openId, setOpenId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  // The copy just made with Make a copy, and the name of the session it was copied from.
  const [copied, setCopied] = useState<{ id: string; from: string } | null>(null);
  const [copiedAfter, setCopiedAfter] = useState<Record<string, string>>({});
  const [sidebarOpen, setSidebarOpen] = useState(() => !isNarrow());
  const [sidebarWidth, setSidebarWidth] = useState(272);
  const [sidebarDrag, setSidebarDrag] = useState<{
    pointerId: number;
    startX: number;
    startWidth: number;
  } | null>(null);

  function resizeSidebar(width: number) {
    setSidebarWidth(Math.min(SIDEBAR_MAX, Math.max(SIDEBAR_MIN, width)));
  }

  useEffect(() => {
    listSessions()
      .then(setSessions)
      .catch(() => setError("Couldn't load your sessions."));
    listFolders()
      .then(setFolders)
      .catch(() => setError("Couldn't load your folders."));
  }, []);

  /** Keeps the sidebar in step: a renamed session stays where it is, a new one goes on top. */
  function remember(session: Session) {
    setSessions((all) =>
      all.some((one) => one.id === session.id)
        ? all.map((one) => (one.id === session.id ? session : one))
        : [session, ...all],
    );
  }

  /** A folder made goes in the list; a renamed one keeps its place. */
  function rememberFolder(folder: Folder) {
    setFolders((all) =>
      all.some((one) => one.id === folder.id)
        ? all.map((one) => (one.id === folder.id ? folder : one))
        : [...all, folder],
    );
  }

  /** A removed folder's sessions are kept, in no folder, as the server keeps them. */
  function forgetFolder(folderId: string) {
    setFolders((all) => all.filter((one) => one.id !== folderId));
    setSessions((all) =>
      all.map((one) => (one.folder === folderId ? { ...one, folder: null } : one)),
    );
  }

  const open = sessions.find((one) => one.id === openId) ?? null;

  /** On a phone the sidebar covers the chat, so picking a session puts it away. */
  function go(sessionId: string | null) {
    setOpenId(sessionId);
    if (isNarrow()) setSidebarOpen(false);
  }

  /** The session list's copy opens the new session below its source, named after it. */
  function openCopy(original: Session, copy: Session) {
    setSessions((all) => all.flatMap((one) => (one.id === original.id ? [one, copy] : [one])));
    setCopiedAfter((all) => ({ ...all, [copy.id]: original.id }));
    setCopied({ id: copy.id, from: sessionTitle(original) });
    go(copy.id);
  }

  return (
    <div
      className={`app ${sidebarOpen ? "sidebar-open" : "sidebar-closed"}`}
      data-resizing={sidebarDrag !== null || undefined}
      style={{ "--session-sidebar-width": `${sidebarWidth}px` } as CSSProperties}
    >
      {error && (
        <p role="alert" className="notice info app-error">
          {error}
        </p>
      )}
      <SessionList
        sessions={sessions}
        copiedAfter={copiedAfter}
        folders={folders}
        openId={openId}
        onOpen={go}
        onNew={() => go(null)}
        onSessionSaved={remember}
        onCopied={openCopy}
        onFolderSaved={rememberFolder}
        onFolderRemoved={forgetFolder}
        onHide={() => setSidebarOpen(false)}
      />
      {sidebarOpen && (
        <>
          <div
            className="sidebar-resize"
            role="separator"
            tabIndex={0}
            aria-label="Resize sessions sidebar"
            aria-orientation="vertical"
            aria-controls="session-sidebar"
            aria-valuemin={SIDEBAR_MIN}
            aria-valuemax={SIDEBAR_MAX}
            aria-valuenow={sidebarWidth}
            onPointerDown={(event) => {
              if (event.button !== 0 || !event.isPrimary) return;
              event.preventDefault();
              event.currentTarget.focus({ preventScroll: true });
              event.currentTarget.setPointerCapture(event.pointerId);
              setSidebarDrag({
                pointerId: event.pointerId,
                startX: event.clientX,
                startWidth: sidebarWidth,
              });
            }}
            onPointerMove={(event) => {
              if (sidebarDrag?.pointerId !== event.pointerId) return;
              resizeSidebar(sidebarDrag.startWidth + event.clientX - sidebarDrag.startX);
            }}
            onLostPointerCapture={() => setSidebarDrag(null)}
            onKeyDown={(event) => {
              switch (event.key) {
                case "ArrowLeft":
                  resizeSidebar(sidebarWidth - SIDEBAR_STEP);
                  break;
                case "ArrowRight":
                  resizeSidebar(sidebarWidth + SIDEBAR_STEP);
                  break;
                case "Home":
                  resizeSidebar(SIDEBAR_MIN);
                  break;
                case "End":
                  resizeSidebar(SIDEBAR_MAX);
                  break;
                default:
                  return;
              }
              event.preventDefault();
            }}
          />
          <div className="backdrop" onClick={() => setSidebarOpen(false)} />
        </>
      )}
      <Chat
        key={openId ?? "new"}
        sessionId={openId}
        title={open ? sessionTitle(open) : null}
        sidebarOpen={sidebarOpen}
        onShowSidebar={() => setSidebarOpen(true)}
        onSent={(sent) => {
          remember(sent.session);
          setOpenId(sent.session.id);
        }}
        copiedFrom={copied !== null && copied.id === openId ? copied.from : null}
      />
    </div>
  );
}

/** A phone-sized window, where the sidebar can't sit beside the chat. */
function isNarrow() {
  return window.innerWidth <= 720;
}
