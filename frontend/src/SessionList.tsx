import { useEffect, useMemo, useRef, useState, type DragEvent, type FormEvent } from "react";
import {
  ApiError,
  makeFolder,
  moveSession,
  removeFolder,
  renameFolder,
  renameSession,
  type Folder,
  type Session,
} from "./api";
import {
  ChevronIcon,
  FolderIcon,
  FolderMoveIcon,
  FolderPlusIcon,
  MoreIcon,
  PencilIcon,
  PlusIcon,
  SidebarIcon,
  SparkIcon,
} from "./icons";
import { Menu, type MenuChoice } from "./Menu";

/** Where the open folders are remembered between visits, in this browser only. */
const OPEN_FOLDERS_KEY = "adforge.open-folders";

/** The sessions on the side: the owner's folders, each holding the sessions filed in it,
 * then every session in no folder, newest first, a way to start another and the one being
 * read. A session has no name until its first message gives it one. A session is filed by
 * its menu or by dragging it onto a folder, and taken out the same ways. */
export function SessionList({
  sessions,
  folders,
  openId,
  onOpen,
  onNew,
  onSessionSaved,
  onFolderSaved,
  onFolderRemoved,
  onHide,
}: {
  sessions: Session[];
  folders: Folder[];
  openId: string | null;
  onOpen: (sessionId: string) => void;
  onNew: () => void;
  /** A session the server changed: renamed or filed in another folder. */
  onSessionSaved: (session: Session) => void;
  /** A folder made or renamed. */
  onFolderSaved: (folder: Folder) => void;
  onFolderRemoved: (folderId: string) => void;
  onHide: () => void;
}) {
  const [renaming, setRenaming] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [opened, setOpened] = useState<Set<string>>(readOpenFolders);
  /** The folder whose name is being typed: "new" for one being made. */
  const [naming, setNaming] = useState<string | null>(null);
  const [folderName, setFolderName] = useState("");
  /** A session to file in the folder being made, when it was made from that session's menu. */
  const [fileAfter, setFileAfter] = useState<string | null>(null);
  const [removing, setRemoving] = useState<string | null>(null);
  /** A folder name on its way to the server, so a second Enter sends nothing. */
  const [savingFolder, setSavingFolder] = useState(false);
  const [menu, setMenu] = useState<{ anchor: HTMLElement; for: string } | null>(null);
  const [dragging, setDraggingState] = useState<string | null>(null);
  // Drag events can come faster than the list redraws, so the drop zones read this.
  const draggingNow = useRef<string | null>(null);
  function setDragging(sessionId: string | null) {
    draggingNow.current = sessionId;
    setDraggingState(sessionId);
  }
  const [dropOn, setDropOn] = useState<string | null>(null);

  useEffect(() => {
    try {
      localStorage.setItem(OPEN_FOLDERS_KEY, JSON.stringify([...opened]));
    } catch {
      // Storage is off, as in a private window: folders just start closed next time.
    }
  }, [opened]);

  function failed(e: unknown) {
    setError(e instanceof ApiError ? e.message : "Couldn't reach the server. Try again.");
  }

  async function save(event: FormEvent) {
    event.preventDefault();
    if (renaming === null) return;
    try {
      onSessionSaved(await renameSession(renaming, name));
      setRenaming(null);
      setError(null);
    } catch (e) {
      failed(e);
    }
  }

  function open(folderId: string) {
    setOpened((now) => new Set(now).add(folderId));
  }

  function toggle(folderId: string) {
    setOpened((now) => {
      const next = new Set(now);
      if (!next.delete(folderId)) next.add(folderId);
      return next;
    });
  }

  async function file(sessionId: string, folderId: string | null) {
    const session = sessions.find((one) => one.id === sessionId);
    if (session === undefined || (session.folder ?? null) === folderId) return;
    try {
      onSessionSaved(await moveSession(sessionId, folderId));
      if (folderId !== null) open(folderId);
      setError(null);
    } catch (e) {
      failed(e);
    }
  }

  function startNaming(folder: Folder | null, filing: string | null = null) {
    setNaming(folder?.id ?? "new");
    setFolderName(folder?.name ?? "");
    setFileAfter(filing);
    setRemoving(null);
    setError(null);
  }

  function stopNaming() {
    setNaming(null);
    setFileAfter(null);
    setError(null);
  }

  async function saveFolder(event: FormEvent) {
    event.preventDefault();
    if (naming === null || savingFolder) return;
    const trimmed = folderName.trim();
    if (trimmed === "") return stopNaming();
    setSavingFolder(true);
    try {
      const saved =
        naming === "new" ? await makeFolder(trimmed) : await renameFolder(naming, trimmed);
      onFolderSaved(saved);
      const filing = fileAfter;
      stopNaming();
      if (filing !== null) {
        onSessionSaved(await moveSession(filing, saved.id));
        open(saved.id);
      }
    } catch (e) {
      failed(e);
    } finally {
      setSavingFolder(false);
    }
  }

  async function remove(folderId: string) {
    try {
      await removeFolder(folderId);
      setRemoving(null);
      setError(null);
      setOpened((now) => {
        const next = new Set(now);
        next.delete(folderId);
        return next;
      });
      onFolderRemoved(folderId);
    } catch (e) {
      failed(e);
    }
  }

  const ordered = useMemo(
    () =>
      [...folders].sort((a, b) =>
        a.name.localeCompare(b.name, undefined, { numeric: true, sensitivity: "base" }),
      ),
    [folders],
  );
  const known = new Set(folders.map((folder) => folder.id));
  const inFolder = (folderId: string) => sessions.filter((one) => one.folder === folderId);
  // A session whose folder the list doesn't know yet shows loose rather than vanishing.
  const loose = sessions.filter((one) => !one.folder || !known.has(one.folder));
  const draggedFrom = sessions.find((one) => one.id === dragging)?.folder ?? null;
  const folderOf = (sessionId: string | null) =>
    sessions.find((one) => one.id === sessionId)?.folder ?? null;

  /** A dragged session may be dropped on a folder, or on the loose list (null), unless it
   * is already there. */
  function dragOver(event: DragEvent, target: string | null) {
    const moving = draggingNow.current;
    if (moving === null) return;
    // Handled here, so the sidebar's own handler below doesn't clear the highlight.
    event.stopPropagation();
    if (folderOf(moving) === target) return setDropOn(null);
    event.preventDefault();
    if (event.dataTransfer) event.dataTransfer.dropEffect = "move";
    setDropOn(target ?? "loose");
  }

  function drop(event: DragEvent, target: string | null) {
    event.preventDefault();
    const moving = draggingNow.current;
    if (moving !== null) void file(moving, target);
    setDragging(null);
    setDropOn(null);
  }

  function sessionRow(session: Session) {
    const active = session.id === openId;
    return (
      <li
        key={session.id}
        className={`session-item${active ? " active" : ""}${session.id === dragging ? " dragging" : ""}`}
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
              aria-current={active}
              title={session.name || "Untitled"}
              draggable
              onDragStart={(event) => {
                setDragging(session.id);
                event.dataTransfer?.setData("text/plain", session.id);
                if (event.dataTransfer) event.dataTransfer.effectAllowed = "move";
              }}
              onDragEnd={() => {
                setDragging(null);
                setDropOn(null);
              }}
            >
              {sessionTitle(session)}
            </button>
            <button
              className={`icon-button${menu?.for === session.id ? " pressed" : ""}`}
              aria-label="Move to folder"
              title="Move to folder"
              aria-haspopup="menu"
              aria-expanded={menu?.for === session.id}
              onClick={(event) =>
                setMenu(
                  menu?.for === session.id
                    ? null
                    : { anchor: event.currentTarget, for: session.id },
                )
              }
            >
              <FolderMoveIcon />
            </button>
            {active && (
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
    );
  }

  function nameForm(label: string) {
    return (
      <form className="rename-form folder-name-form" onSubmit={saveFolder}>
        <FolderIcon size={15} />
        <input
          aria-label="Folder name"
          placeholder={label}
          value={folderName}
          maxLength={100}
          onChange={(e) => setFolderName(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Escape") stopNaming();
          }}
          autoFocus
        />
        <button type="submit" className="text-button primary" disabled={savingFolder}>
          Save
        </button>
      </form>
    );
  }

  function menuChoices(key: string): MenuChoice[] {
    const folder = folders.find((one) => `folder:${one.id}` === key);
    if (folder !== undefined) {
      return [
        {
          kind: "item",
          label: "Rename folder",
          icon: <PencilIcon />,
          onPick: () => startNaming(folder),
        },
        {
          kind: "item",
          label: "Remove folder",
          danger: true,
          icon: <span aria-hidden>×</span>,
          onPick: () => {
            setNaming(null);
            setRemoving(folder.id);
          },
        },
      ];
    }
    const session = sessions.find((one) => one.id === key);
    if (session === undefined) return [];
    const current = session.folder ?? null;
    return [
      ...(ordered.length > 0 ? [{ kind: "heading" as const, label: "Move to" }] : []),
      ...ordered.map((folder): MenuChoice => ({
        kind: "radio",
        label: folder.name,
        checked: folder.id === current,
        icon: <FolderIcon size={14} />,
        onPick: () => void file(session.id, folder.id),
      })),
      ...(ordered.length > 0 ? [{ kind: "separator" as const }] : []),
      {
        kind: "item",
        label: "New folder…",
        icon: <FolderPlusIcon size={14} />,
        onPick: () => startNaming(null, session.id),
      },
      ...(current !== null && known.has(current)
        ? [
            {
              kind: "item" as const,
              label: "Take out of folder",
              icon: <span aria-hidden>↩</span>,
              onPick: () => void file(session.id, null),
            },
          ]
        : []),
    ];
  }

  const groups = groupByDay(loose);
  const closeMenu = () => setMenu(null);

  return (
    <nav
      aria-label="Sessions"
      className="sidebar"
      // A drag over no place to drop it (dragover fires all the time a drag goes on)
      // takes the highlight off; so does one leaving the sidebar.
      onDragOver={() => setDropOn(null)}
      onDragLeave={(event) => {
        const box = event.currentTarget.getBoundingClientRect();
        const { clientX: x, clientY: y } = event;
        if (x <= box.left || x >= box.right || y <= box.top || y >= box.bottom) setDropOn(null);
      }}
    >
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
          <p role="alert" className="notice info" style={{ marginTop: 10 }}>
            {error}
          </p>
        )}

        <div className="session-groups">
          <div className="session-group-title folders-title">
            <span>Folders</span>
            <button
              className="icon-button small"
              aria-label="New folder"
              title="New folder"
              onClick={() => startNaming(null)}
            >
              <FolderPlusIcon size={15} />
            </button>
          </div>
          {naming === "new" &&
            nameForm(fileAfter === null ? "Folder name" : "New folder for this session")}
          <ul aria-label="Folders" className="folder-list">
            {ordered.map((folder) => {
              const members = inFolder(folder.id);
              const isOpen = opened.has(folder.id);
              const count = `${members.length} session${members.length === 1 ? "" : "s"}`;
              return (
                <li
                  key={folder.id}
                  className={`folder-item${dropOn === folder.id ? " drop-target" : ""}${isOpen ? " open" : ""}`}
                  onDragOver={(event) => dragOver(event, folder.id)}
                  onDrop={(event) => drop(event, folder.id)}
                >
                  {naming === folder.id ? (
                    nameForm("Folder name")
                  ) : (
                    <div className="folder-row">
                      <button
                        className="folder-toggle"
                        aria-label={`${folder.name}, ${count}`}
                        aria-expanded={isOpen}
                        title={folder.name}
                        onClick={() => toggle(folder.id)}
                      >
                        <span className="folder-chevron">
                          <ChevronIcon size={14} />
                        </span>
                        <FolderIcon size={15} />
                        <span className="folder-name">{folder.name}</span>
                        <span className="folder-count">{members.length}</span>
                      </button>
                      <button
                        className={`icon-button${menu?.for === `folder:${folder.id}` ? " pressed" : ""}`}
                        aria-label="Folder options"
                        title="Folder options"
                        aria-haspopup="menu"
                        aria-expanded={menu?.for === `folder:${folder.id}`}
                        onClick={(event) =>
                          setMenu(
                            menu?.for === `folder:${folder.id}`
                              ? null
                              : { anchor: event.currentTarget, for: `folder:${folder.id}` },
                          )
                        }
                      >
                        <MoreIcon />
                      </button>
                    </div>
                  )}
                  {removing === folder.id && (
                    <div className="folder-remove" role="group" aria-label="Remove folder">
                      <p>
                        Remove “{folder.name}”?{" "}
                        {members.length === 0
                          ? "It's empty."
                          : `Its ${count} ${members.length === 1 ? "stays" : "stay"}, outside any folder.`}
                      </p>
                      <div className="folder-remove-actions">
                        <button className="text-button" onClick={() => setRemoving(null)}>
                          Cancel
                        </button>
                        <button
                          className="text-button danger"
                          onClick={() => void remove(folder.id)}
                        >
                          Remove
                        </button>
                      </div>
                    </div>
                  )}
                  {isOpen &&
                    (members.length > 0 ? (
                      <ul aria-label={folder.name} className="session-list in-folder">
                        {members.map(sessionRow)}
                      </ul>
                    ) : (
                      <p className="folder-empty">
                        Empty. Drag a session here, or use its <FolderMoveIcon size={12} /> button.
                      </p>
                    ))}
                </li>
              );
            })}
          </ul>
          {ordered.length === 0 && naming !== "new" && (
            <p className="folder-empty top">Group sessions, as one folder per round.</p>
          )}

          <section
            aria-label="Not in a folder"
            className={`loose-sessions${dropOn === "loose" ? " drop-target" : ""}`}
            onDragOver={(event) => dragOver(event, null)}
            onDrop={(event) => drop(event, null)}
          >
            {dragging !== null && draggedFrom !== null && (
              <div className="drop-hint">Drop here to take it out of its folder</div>
            )}
            {groups.map(([title, members]) => (
              <div key={title}>
                <div className="session-group-title">{title}</div>
                <ul className="session-list">{members.map(sessionRow)}</ul>
              </div>
            ))}
          </section>
        </div>

        <div className="sidebar-foot">One producer. One box. One finished ad.</div>
      </div>
      {menu && (
        <Menu
          anchor={menu.anchor}
          label={menu.for.startsWith("folder:") ? "Folder options" : "Move to folder"}
          choices={menuChoices(menu.for)}
          onClose={closeMenu}
        />
      )}
    </nav>
  );
}

/** The folders left open last time, or none. */
function readOpenFolders(): Set<string> {
  try {
    const kept: unknown = JSON.parse(localStorage.getItem(OPEN_FOLDERS_KEY) ?? "[]");
    return new Set(Array.isArray(kept) ? kept.map(String) : []);
  } catch {
    return new Set();
  }
}

/** What a session is called on screen. The server names it after the first thing the owner
 * typed, which is usually the product's link, and links all look alike, so a name that
 * starts with one shows the product the link names and the shop. Any other name, as one
 * the owner gave it, shows as it is. */
export function sessionTitle(session: Session): string {
  if (session.name === "") return "Untitled";
  const [first] = session.name.split(" ");
  if (!/^(https?:\/\/|www\.)/i.test(first)) return session.name;
  let link: URL;
  try {
    link = new URL(/^www\./i.test(first) ? `https://${first}` : first);
  } catch {
    return session.name;
  }
  // The shop as typed, since the parsed one spells a name like müller.de in punycode.
  const shop = first
    .replace(/^https?:\/\//i, "")
    .split(/[/?#:]/)[0]
    .replace(/^www\./i, "");
  const product = productIn(link.pathname);
  return product ? `${product} · ${shop}` : shop;
}

/** Path parts that say where a product is, not which. */
const NOT_A_PRODUCT = new Set([
  "products",
  "product",
  "p",
  "dp",
  "gp",
  "ip",
  "item",
  "items",
  "itm",
  "listing",
  "ref",
  "shop",
]);

/** A code, not a name: "B07X4Q2ABC", "A-87654321", "123456789". */
const CODE = /^[a-z]{0,3}[-_]?\d[\da-z]*$/i;

/** The product a link's path names, in words: its last part that is a name, as
 * "/products/mens-tree-runners" or "/Bamboo-Toothbrush/dp/B07X4Q2ABC". A word the server
 * cut short ("…") is left out. Null for none. */
function productIn(path: string): string | null {
  const names = path
    .split("/")
    .map((part) => decoded(part).replace(/\.html?$/i, ""))
    .filter(
      (part) =>
        /[a-z]/i.test(part) &&
        !part.includes("=") &&
        !CODE.test(part) &&
        !NOT_A_PRODUCT.has(part.toLowerCase()),
    )
    .map((part) => part.split(/[-_ ]+/).filter((word) => word !== "" && !word.endsWith("…")))
    .filter((words) => words.length > 0);
  const named = names.at(-1);
  if (named === undefined) return null;
  return named.map((word) => word[0].toUpperCase() + word.slice(1)).join(" ");
}

/** A path part as typed, its %-escapes read; one with a broken escape is kept as it is. */
function decoded(part: string): string {
  try {
    return decodeURIComponent(part);
  } catch {
    return part;
  }
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
