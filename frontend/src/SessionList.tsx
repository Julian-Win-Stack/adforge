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
                          {sessionTitle(session)}
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
