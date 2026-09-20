import { useEffect, useRef, useState, type FormEvent } from "react";
import {
  ApiError,
  getMessagesSince,
  sendMessage,
  startSession,
  type Attachment,
  type Message,
  type Sent,
} from "./api";

const POLL_EVERY_MS = 2000;

/** One session's chat: everything said so far, and the box for saying the next thing. Each poll
 * asks only for messages after the last one on screen, so nothing is skipped or shown
 * twice; the first poll asks from the start, which is how a session is reopened with its
 * whole history. Render it with `key={sessionId}` so another session starts empty. */
export function Chat({
  sessionId,
  onSent,
}: {
  sessionId: string | null;
  onSent: (sent: Sent) => void;
}) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [pollError, setPollError] = useState<string | null>(null);
  const lastSeq = useRef(0);
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

  /** Says something. The first message of a new session is what creates the session. */
  async function send(text: string, photos: File[]) {
    const id = sessionId ?? (started.current ??= (await startSession()).id);
    const sent = await sendMessage(id, text, photos);
    if (sessionId !== null) show([sent.message]);
    // A brand new session has no polling yet: opening it loads what was just said.
    onSent(sent);
  }

  return (
    <section style={{ flexGrow: 1 }}>
      {pollError && <p style={{ color: "crimson" }}>{pollError}</p>}
      <ol aria-label="Conversation" style={{ listStyle: "none", padding: 0 }}>
        {messages.map((message) => (
          <li key={message.seq} style={{ marginBottom: "1rem" }}>
            <strong>{message.role === "user" ? "You" : "Agent"}</strong>
            {message.text && <p style={{ margin: "0.25rem 0" }}>{message.text}</p>}
            {message.attachments.map((attachment) => (
              <AttachmentView
                key={attachment.position}
                attachment={attachment}
                role={message.role}
              />
            ))}
          </li>
        ))}
      </ol>
      <Composer onSend={send} />
    </section>
  );
}

/** What a message carries: a picture shows, a sound plays, a video can be watched. */
function AttachmentView({ attachment, role }: { attachment: Attachment; role: Message["role"] }) {
  const described = role === "user" ? "Photo you attached" : "Picture the agent made";
  if (attachment.kind === "picture") {
    return (
      <img
        src={attachment.url}
        alt={`${described} (${attachment.position})`}
        style={{ maxWidth: 320, display: "block" }}
      />
    );
  }
  if (attachment.kind === "sound") {
    return <audio controls src={attachment.url} aria-label="Sound the agent made" />;
  }
  return (
    <video
      controls
      src={attachment.url}
      aria-label="Video the agent made"
      style={{ maxWidth: 480, display: "block" }}
    />
  );
}

/** The one box the whole app is driven from: free text, and photos when there are any.
 * It stays open while the agent works, so the user is never locked out of answering. */
function Composer({ onSend }: { onSend: (text: string, photos: File[]) => Promise<void> }) {
  const [text, setText] = useState("");
  const [photos, setPhotos] = useState<File[]>([]);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const photoInput = useRef<HTMLInputElement>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setSending(true);
    setError(null);
    try {
      await onSend(text, photos);
      setText("");
      setPhotos([]);
      if (photoInput.current) photoInput.current.value = "";
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Couldn't reach the server. Try again.");
    } finally {
      setSending(false);
    }
  }

  return (
    <form onSubmit={submit}>
      <label>
        Message
        <textarea
          value={text}
          onChange={(event) => setText(event.target.value)}
          rows={3}
          style={{ display: "block", width: "100%" }}
        />
      </label>
      <label>
        Photos
        <input
          ref={photoInput}
          type="file"
          multiple
          accept="image/jpeg,image/png,image/webp,image/gif"
          onChange={(event) => setPhotos(Array.from(event.target.files ?? []))}
        />
      </label>
      <button type="submit" disabled={sending}>
        Send
      </button>
      {error && <p style={{ color: "crimson" }}>{error}</p>}
    </form>
  );
}
