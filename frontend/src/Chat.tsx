import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import {
  ApiError,
  getMessagesSince,
  sendMessage,
  startSession,
  type Attachment,
  type Message,
  type Sent,
} from "./api";
import { ArrowUpIcon, CloseIcon, ImageIcon, SidebarIcon, SoundIcon, SparkIcon } from "./icons";

const POLL_EVERY_MS = 2000;

const SUGGESTIONS = [
  "Make a 15-second TikTok ad for a product page",
  "Show the price and a promo code on screen",
  "End with “Shop now”",
];

/** One session's chat: everything said so far, and the box for saying the next thing. Each poll
 * asks only for messages after the last one on screen, so nothing is skipped or shown
 * twice; the first poll asks from the start, which is how a session is reopened with its
 * whole history. Render it with `key={sessionId}` so another session starts empty. */
export function Chat({
  sessionId,
  title,
  sidebarOpen,
  onShowSidebar,
  onSent,
}: {
  sessionId: string | null;
  title: string | null;
  sidebarOpen: boolean;
  onShowSidebar: () => void;
  onSent: (sent: Sent) => void;
}) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [pollError, setPollError] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const lastSeq = useRef(0);
  const thread = useRef<HTMLDivElement>(null);
  // A new session is made once, so a refused first message doesn't leave another empty
  // session behind on every retry.
  const started = useRef<string | null>(null);

  /** Adds what the page hasn't shown yet. A poll that was already on its way when a
   * message was sent comes back holding that message too, so anything at or below the
   * last number shown is left out. */
  function show(arrived: Message[]) {
    const unseen = arrived.filter((message) => message.seq > lastSeq.current);
    if (unseen.length === 0) return;
    lastSeq.current = unseen[unseen.length - 1].seq;
    setMessages((shown) => [...shown, ...unseen]);
  }

  useEffect(() => {
    if (sessionId === null) return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;

    async function ask() {
      try {
        const newer = await getMessagesSince(sessionId as string, lastSeq.current);
        if (stopped) return;
        show(newer);
        setPollError(null);
      } catch {
        if (stopped) return;
        setPollError("Lost contact with the server. Still trying...");
      }
      timer = setTimeout(ask, POLL_EVERY_MS);
    }

    ask();
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
  }, [sessionId]);

  // The thread follows the newest message, unless the reader has scrolled up to reread
  // something; then it stays put until they come back down. Pictures and videos that
  // finish loading make the thread taller, so growth is watched, not just new messages.
  const following = useRef(true);
  useEffect(() => {
    const el = thread.current;
    if (!el) return;
    const nearBottom = () => el.scrollHeight - el.scrollTop - el.clientHeight < 80;
    const onScroll = () => {
      following.current = nearBottom();
    };
    el.addEventListener("scroll", onScroll);
    const grew = () => {
      if (following.current) el.scrollTop = el.scrollHeight;
    };
    const watcher = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(grew);
    if (el.firstElementChild) watcher?.observe(el.firstElementChild);
    return () => {
      el.removeEventListener("scroll", onScroll);
      watcher?.disconnect();
    };
  }, []);
  useEffect(() => {
    const el = thread.current;
    if (el && following.current) el.scrollTop = el.scrollHeight;
  }, [messages.length]);

  /** Says something. The first message of a new session is what creates the session. */
  async function send(text: string, photos: File[]) {
    const id = sessionId ?? (started.current ??= (await startSession()).id);
    const sent = await sendMessage(id, text, photos);
    if (sessionId !== null) show([sent.message]);
    // A brand new session has no polling yet: opening it loads what was just said.
    onSent(sent);
  }

  const empty = sessionId === null && messages.length === 0;
  const producerHasTheFloor = sessionId !== null && messages.at(-1)?.role === "user";

  return (
    <section className="chat">
      <header className="chat-top">
        {!sidebarOpen && (
          <button className="icon-button" onClick={onShowSidebar} aria-label="Show sessions">
            <SidebarIcon />
          </button>
        )}
        <span className="chat-title">{title ?? (sessionId ? "Untitled" : "New session")}</span>
      </header>

      <div className="thread" ref={thread}>
        <div className="thread-inner">
          {pollError && (
            <p role="status" className="notice error thread-notice">
              {pollError}
            </p>
          )}

          {empty && (
            <div className="hero">
              <span className="hero-mark">
                <SparkIcon size={26} />
              </span>
              <h1>What are we making today?</h1>
              <p>
                Paste a product link and say what the ad should do. The producer reads the page,
                plans the scenes and hands you a finished video.
              </p>
              <div className="suggestions">
                {SUGGESTIONS.map((suggestion) => (
                  <button
                    key={suggestion}
                    className="suggestion"
                    onClick={() => setDraft((was) => (was ? `${was} ${suggestion}` : suggestion))}
                  >
                    {suggestion}
                  </button>
                ))}
              </div>
            </div>
          )}

          <ol aria-label="Conversation" className="messages">
            {messages.map((message) => (
              <li key={message.seq} className={`message ${message.role}`}>
                <strong className="who">
                  {message.role === "agent" && (
                    <span className="who-mark">
                      <SparkIcon size={11} />
                    </span>
                  )}
                  {message.role === "user" ? "You" : "Agent"}
                </strong>
                {message.text &&
                  (message.role === "user" ? (
                    <p className="bubble">{message.text}</p>
                  ) : (
                    <p className="prose">{message.text}</p>
                  ))}
                {message.attachments.length > 0 && (
                  <div className="attachments">
                    {message.attachments.map((attachment) => (
                      <AttachmentView
                        key={attachment.position}
                        attachment={attachment}
                        role={message.role}
                      />
                    ))}
                  </div>
                )}
              </li>
            ))}
          </ol>

          {producerHasTheFloor && (
            <div className="working" aria-live="polite">
              <span className="dots">
                <i />
                <i />
                <i />
              </span>
              The producer is working
            </div>
          )}
        </div>
      </div>

      <div className="composer-wrap">
        <Composer text={draft} onChange={setDraft} onSend={send} />
        <p className="composer-foot">
          The producer only states what the page says, plus what you tell it.
        </p>
      </div>
    </section>
  );
}

/** What a message carries: a picture shows, a sound plays, a video can be watched. */
function AttachmentView({ attachment, role }: { attachment: Attachment; role: Message["role"] }) {
  const described = role === "user" ? "Photo you attached" : "Picture the agent made";
  if (attachment.kind === "picture") {
    return (
      <a href={attachment.url} target="_blank" rel="noreferrer">
        <img
          className="picture"
          src={attachment.url}
          alt={`${described} (${attachment.position})`}
          loading="lazy"
        />
      </a>
    );
  }
  if (attachment.kind === "sound") {
    return (
      <div className="sound">
        <span className="sound-mark">
          <SoundIcon />
        </span>
        <audio controls src={attachment.url} aria-label="Sound the agent made" />
      </div>
    );
  }
  return (
    <video
      className="video"
      controls
      playsInline
      src={attachment.url}
      aria-label="Video the agent made"
    />
  );
}

/** The one box the whole app is driven from: free text, and photos when there are any.
 * It stays open while the agent works, so the user is never locked out of answering. */
function Composer({
  text,
  onChange,
  onSend,
}: {
  text: string;
  onChange: (text: string) => void;
  onSend: (text: string, photos: File[]) => Promise<void>;
}) {
  const [photos, setPhotos] = useState<File[]>([]);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const photoInput = useRef<HTMLInputElement>(null);
  const box = useRef<HTMLTextAreaElement>(null);

  // The box grows with what's typed, up to a few lines, then scrolls.
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 200)}px`;
  }, [text]);

  const canSend = !sending && (text.trim().length > 0 || photos.length > 0);

  async function submit(event?: FormEvent) {
    event?.preventDefault();
    if (!canSend) return;
    setSending(true);
    setError(null);
    try {
      await onSend(text, photos);
      onChange("");
      setPhotos([]);
      if (photoInput.current) photoInput.current.value = "";
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Couldn't reach the server. Try again.");
    } finally {
      setSending(false);
    }
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      submit();
    }
  }

  function addPhotos(picked: FileList | null) {
    const files = Array.from(picked ?? []);
    if (files.length === 0) return;
    setPhotos((have) => [...have, ...files]);
  }

  function dropPhoto(index: number) {
    setPhotos((have) => have.filter((_, i) => i !== index));
    if (photoInput.current) photoInput.current.value = "";
  }

  return (
    <form className="composer" onSubmit={submit}>
      <textarea
        ref={box}
        aria-label="Message"
        placeholder="Paste a product link and describe the ad you want..."
        value={text}
        onChange={(event) => onChange(event.target.value)}
        onKeyDown={onKeyDown}
        rows={1}
      />
      {error && (
        <p role="alert" className="notice error inline">
          {error}
        </p>
      )}
      <div className="composer-row">
        <input
          ref={photoInput}
          className="visually-hidden"
          aria-label="Photos"
          type="file"
          multiple
          accept="image/jpeg,image/png,image/webp,image/gif"
          onChange={(event) => addPhotos(event.target.files)}
          tabIndex={-1}
        />
        <button
          type="button"
          className="attach"
          aria-label="Attach photos"
          title="Attach photos"
          onClick={() => photoInput.current?.click()}
        >
          <ImageIcon />
          {photos.length > 0 && <span className="count">{photos.length}</span>}
        </button>
        <div className="chips">
          {photos.map((photo, index) => (
            <span key={`${photo.name}-${index}`} className="chip">
              <span>{photo.name}</span>
              <button
                type="button"
                aria-label={`Remove ${photo.name}`}
                onClick={() => dropPhoto(index)}
              >
                <CloseIcon />
              </button>
            </span>
          ))}
        </div>
        <span className="composer-hint">Enter to send</span>
        <button type="submit" className="send" aria-label="Send" disabled={!canSend}>
          {sending ? <span className="spinner" /> : <ArrowUpIcon />}
        </button>
      </div>
    </form>
  );
}
