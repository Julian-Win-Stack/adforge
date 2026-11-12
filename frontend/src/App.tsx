import { useEffect, useState } from "react";
import { Chat } from "./Chat";
import { SessionList, sessionTitle } from "./SessionList";
import { listFolders, listSessions, type Folder, type Session } from "./api";

export function App() {
  const [sessions, setSessions] = useState<Session[]>([]);
  const [folders, setFolders] = useState<Folder[]>([]);
  const [openId, setOpenId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  // The copy just made with Make a copy, and the name of the session it was copied from.
  const [copied, setCopied] = useState<{ id: string; from: string } | null>(null);
  const [copiedAfter, setCopiedAfter] = useState<Record<string, string>>({});
  const [sidebarOpen, setSidebarOpen] = useState(() => !isNarrow());

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

  /** Both copy actions open the new session with the name of its actual source. */
  function openCopy(original: Session, copy: Session, fromList = false) {
    if (fromList) {
      setSessions((all) => all.flatMap((one) => (one.id === original.id ? [one, copy] : [one])));
      setCopiedAfter((all) => ({ ...all, [copy.id]: original.id }));
    } else {
      remember(copy);
    }
    setCopied({ id: copy.id, from: sessionTitle(original) });
    go(copy.id);
  }

  return (
    <div className={`app ${sidebarOpen ? "sidebar-open" : "sidebar-closed"}`}>
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
        onCopied={(original, copy) => openCopy(original, copy, true)}
        onFolderSaved={rememberFolder}
        onFolderRemoved={forgetFolder}
        onHide={() => setSidebarOpen(false)}
      />
      {sidebarOpen && <div className="backdrop" onClick={() => setSidebarOpen(false)} />}
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
        onCopied={(copy) => {
          if (open) openCopy(open, copy);
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
