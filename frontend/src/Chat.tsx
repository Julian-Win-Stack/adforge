import {
  useEffect,
  useRef,
  useState,
  type FormEvent,
  type KeyboardEvent,
  type ReactNode,
} from "react";
import {
  ApiError,
  getMessagesSince,
  sendMessage,
  startSession,
  type Attachment,
  type Message,
  type PlanNotice,
  type PlanScene,
  type Sent,
} from "./api";
import { ScenePanel } from "./ScenePanel";
import { nextFollowing } from "./follow";
import { planOnOffer, suggestedNotes, waitsForYes } from "./scenes";
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
  // Where the thread was at the last scroll event, or where the code last put it. Its own
  // scrolling is told apart from the reader's by this: a scroll event can land after more
  // has loaded below, which isn't the reader leaving the bottom; only scrolling up is.
  const placedAt = useRef(0);
  const toBottom = (el: HTMLElement) => {
    el.scrollTop = el.scrollHeight;
    placedAt.current = el.scrollTop;
  };
  useEffect(() => {
    const el = thread.current;
    if (!el) return;
    const nearBottom = () => el.scrollHeight - el.scrollTop - el.clientHeight < 80;
    const onScroll = () => {
      following.current = nextFollowing({
        top: el.scrollTop,
        placedAt: placedAt.current,
        nearBottom: nearBottom(),
        following: following.current,
      });
      placedAt.current = el.scrollTop;
    };
    el.addEventListener("scroll", onScroll);
    const grew = () => {
      if (following.current) toBottom(el);
    };
    const watcher = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(grew);
    if (el.firstElementChild) watcher?.observe(el.firstElementChild);
    // The thread itself gets shorter when something opens beside it, like the scene cards
    // above it on a phone; the newest message must stay in view then too.
    watcher?.observe(el);
    return () => {
      el.removeEventListener("scroll", onScroll);
      watcher?.disconnect();
    };
  }, []);
  useEffect(() => {
    const el = thread.current;
    if (el && following.current) toBottom(el);
  }, [messages.length]);

  /** Says something. The first message of a new session is what creates the session. A
   * plan's yes button names its `plan`. */
  async function send(text: string, photos: File[], plan?: number) {
    const id = sessionId ?? (started.current ??= (await startSession()).id);
    const sent = await sendMessage(id, text, photos, plan);
    if (sessionId !== null) show([sent.message]);
    // A brand new session has no polling yet: opening it loads what was just said.
    onSent(sent);
  }

  const empty = sessionId === null && messages.length === 0;
  // A notice posted while the producer works doesn't mean it has answered, nor does a scene's
  // picture or clip shown as it is made: look past them.
  const lastSaid = [...messages]
    .reverse()
    .find((message) => message.role !== "notice" && message.step?.state !== "made");
  const producerHasTheFloor = sessionId !== null && lastSaid?.role === "user";
  const offered = planOnOffer(messages);
  // A plan on offer or a suggested note is a question to the owner: nothing runs until they
  // answer, though their last words are still the newest.
  const waitingOnOwner = offered !== null || suggestedNotes(messages).size > 0;
  const covers = clipCovers(messages);

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

      <div className="chat-body">
        <div className="chat-main">
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
                        onClick={() =>
                          setDraft((was) => (was ? `${was} ${suggestion}` : suggestion))
                        }
                      >
                        {suggestion}
                      </button>
                    ))}
                  </div>
                </div>
              )}

              <ol aria-label="Conversation" className="messages">
                {joinStarts(messages).map((message) =>
                  message.plan?.event === "shown" && message.plan.notice !== null ? (
                    <li key={message.seq} className="message notice-message">
                      <PlanView
                        plan={message.plan.notice}
                        onYes={
                          offered?.seq === message.seq
                            ? () => send("Yes", [], offered.plan.plan)
                            : null
                        }
                      />
                    </li>
                  ) : message.role === "notice" ? (
                    <li key={message.seq} className="message notice-message">
                      <NoticeText
                        text={message.text}
                        tone={message.level === "problem" ? "error" : "info"}
                      />
                      {message.attachments.length > 0 && (
                        <div className="attachments">
                          {message.attachments.map((attachment) => (
                            <AttachmentView
                              key={attachment.position}
                              attachment={attachment}
                              role={message.role}
                              covers={covers}
                            />
                          ))}
                        </div>
                      )}
                    </li>
                  ) : (
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
                              covers={covers}
                            />
                          ))}
                        </div>
                      )}
                    </li>
                  ),
                )}
              </ol>

              {producerHasTheFloor && waitingOnOwner && (
                <div className="working" aria-live="polite">
                  Waiting for your answer
                </div>
              )}
              {producerHasTheFloor && !waitingOnOwner && (
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
            {waitsForYes(messages) && <YesBar onYes={() => send("Yes", [])} />}
            <Composer
              text={draft}
              onChange={setDraft}
              onSend={send}
              placeholder={
                sessionId === null
                  ? "Paste a product link and describe the ad you want..."
                  : "Write a message or answer the producer..."
              }
            />
            <p className="composer-foot">
              The producer only states what the page says, plus what you tell it.
            </p>
          </div>
        </div>
        <ScenePanel messages={messages} onSend={(text) => send(text, [])} />
      </div>
    </section>
  );
}

/** Offered when the producer has said back what it understood from the owner's notes: one
 * tap says yes, the same as typing it. A correction is typed in the box below. */
function YesBar({ onYes }: { onYes: () => Promise<void> }) {
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function yes() {
    setSending(true);
    setError(null);
    try {
      await onYes();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Couldn't reach the server. Try again.");
    } finally {
      setSending(false);
    }
  }

  return (
    <div className="yes-bar" role="group" aria-label="Confirm the fix">
      <p className="yes-ask">
        Is that right? <span>Or type what to change below.</span>
      </p>
      {error && (
        <p role="alert" className="notice error inline">
          {error}
        </p>
      )}
      <button type="button" className="yes-button" disabled={sending} onClick={yes}>
        Yes
      </button>
    </div>
  );
}

const ACTIONS: Record<PlanScene["action"], string> = {
  new_picture: "New starting picture",
  new_clip: "New clip",
  new_line: "New line",
  go_back: "Go back",
  no_change: "No change",
};

/** Dollars to the cent, from the server's amount. */
function dollars(amount: string) {
  return `$${Number(amount).toFixed(2)}`;
}

/** What a plan's scene costs, or that it is free. */
function cost(about: string, upTo: string) {
  if (Number(about) === 0 && Number(upTo) === 0) return "Free";
  return `About ${dollars(about)}, up to ${dollars(upTo)}`;
}

/** The plan code posts from the owner's notes: per scene what changes in their words, what
 * it costs and what can't be done, the one question, and the total. Its yes button
 * (`onYes`) is there only while the plan is the one on offer; a correction is typed below. */
function PlanView({ plan, onYes }: { plan: PlanNotice; onYes: (() => Promise<void>) | null }) {
  return (
    <div className="plan" role="group" aria-label="Plan for your notes">
      <p className="plan-title">Here's the plan for your notes</p>
      <ul className="plan-scenes">
        {plan.scenes.map((scene) => (
          <li key={scene.scene} className="plan-scene">
            <span className="plan-scene-head">
              <strong>Scene {scene.scene}</strong>{" "}
              {scene.action === "go_back"
                ? `Go back to version ${scene.go_back_to}`
                : ACTIONS[scene.action]}
            </span>
            {scene.change && <span className="plan-change">{scene.change}</span>}
            {scene.line && <span className="plan-line">“{scene.line}”</span>}
            {scene.overlay && <span className="plan-line">On screen: “{scene.overlay}”</span>}
            {scene.photos.map((photo) => (
              <span key={photo.photo} className="plan-photo">
                Photo {photo.photo}: {photo.thing}
              </span>
            ))}
            {scene.cant ? (
              <span className="plan-cant">Can't: {scene.cant}</span>
            ) : (
              <span className="plan-cost">
                {cost(scene.estimate_usd, scene.estimate_up_to_usd)}
              </span>
            )}
          </li>
        ))}
      </ul>
      {plan.cants.map((cant) => (
        <p key={cant.kind} className="plan-cant">
          Can't: {cant.text}
        </p>
      ))}
      {plan.question && <p className="plan-question">{plan.question}</p>}
      {Number(plan.estimate_up_to_usd) > 0 && (
        <p className="plan-total">
          About {dollars(plan.estimate_usd)} in all, up to {dollars(plan.estimate_up_to_usd)}
        </p>
      )}
      {onYes && <PlanYes onYes={onYes} />}
    </div>
  );
}

/** One tap says yes to the plan; the message it sends is marked as the button's. */
function PlanYes({ onYes }: { onYes: () => Promise<void> }) {
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function yes() {
    setSending(true);
    setError(null);
    try {
      await onYes();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Couldn't reach the server. Try again.");
    } finally {
      setSending(false);
    }
  }

  return (
    <div className="plan-yes">
      {error && (
        <p role="alert" className="notice error">
          {error}
        </p>
      )}
      <button type="button" className="yes-button" disabled={sending} onClick={yes}>
        Yes, go ahead
      </button>
      <span className="plan-or">Or type what to change below.</span>
    </div>
  );
}

/** Starting pictures by scene, to cover each clip before it plays: the picture of the clip's
 * own version (`"3 v2 black toilet"`), else the scene's newest picture (`"3"`). */
type Covers = Map<string, string>;

function clipCovers(messages: Message[]): Covers {
  const covers: Covers = new Map();
  for (const message of messages) {
    for (const attachment of message.attachments) {
      if (attachment.kind !== "picture" || attachment.scene === null) continue;
      covers.set(`${attachment.scene}`, attachment.url);
      if (attachment.version)
        covers.set(`${attachment.scene} ${attachment.version}`, attachment.url);
    }
  }
  return covers;
}

/** Notices that scenes' starting pictures or clips started, back to back, read as one line:
 * "Making pictures for scenes 1-5" for five "Making scene N's starting picture." notices. The
 * joined notice keeps the first one's number; any other message is as it was. */
function joinStarts(messages: Message[]): Message[] {
  const joined: { message: Message; scenes: number[] }[] = [];
  for (const message of messages) {
    const previous = joined[joined.length - 1];
    if (previous && startsAlike(previous.message, message)) {
      previous.scenes.push(message.step!.scene);
      continue;
    }
    joined.push({ message, scenes: message.step ? [message.step.scene] : [] });
  }
  return joined.map(({ message, scenes }) =>
    scenes.length < 2 ? message : { ...message, text: startsLine(message, scenes) },
  );
}

/** Whether `next` starts the same kind of step as `first`, made for the first time or again. */
function startsAlike(first: Message, next: Message) {
  const isStart = (one: Message) => one.role === "notice" && one.step?.state === "started";
  return (
    isStart(first) &&
    isStart(next) &&
    first.step!.kind === next.step!.kind &&
    remaking(first) === remaking(next)
  );
}

function remaking(message: Message) {
  return message.text.startsWith("Remaking");
}

function startsLine(message: Message, scenes: number[]) {
  const what = message.step!.kind === "clip" ? "clips" : "pictures";
  return `${remaking(message) ? "Remaking" : "Making"} ${what} for scenes ${sceneList(scenes)}`;
}

/** Scene numbers as a reader says them: "1-5" for a run, else "2, 4". */
function sceneList(scenes: number[]) {
  const run = scenes.every((scene, i) => i === 0 || scene === scenes[i - 1] + 1);
  return run ? `${scenes[0]}-${scenes[scenes.length - 1]}` : scenes.join(", ");
}

/** A notice's text. A long one, like every photo the page couldn't give, shows its first line
 * and the rest only when asked for, so it doesn't fill the chat. */
function NoticeText({ text, tone }: { text: string; tone: "error" | "info" }) {
  const [open, setOpen] = useState(false);
  const [first, ...rest] = text.split("\n");
  if (rest.length === 0) {
    return (
      <p role="status" className={`notice ${tone}`}>
        {text}
      </p>
    );
  }
  return (
    <div role="status" className={`notice ${tone} notice-long`}>
      <span>{first}</span>
      {open && <span className="notice-details">{rest.join("\n")}</span>}
      <button type="button" className="notice-toggle" onClick={() => setOpen((was) => !was)}>
        {open ? "Hide details" : "Show details"}
      </button>
    </div>
  );
}

/** What a message carries: a picture shows, a sound plays, a video can be watched. A scene's
 * picture or clip is labelled with its scene and version, and a clip is covered with its
 * picture until played; the agent's video of no scene is the finished ad. */
function AttachmentView({
  attachment,
  role,
  covers,
}: {
  attachment: Attachment;
  role: Message["role"];
  covers: Covers;
}) {
  const described = role === "user" ? "Photo you attached" : "Picture the agent made";
  const label =
    attachment.scene === null
      ? null
      : `Scene ${attachment.scene}${attachment.version ? ` · ${attachment.version}` : ""}`;
  const labelled = (media: ReactNode) =>
    label === null ? (
      media
    ) : (
      <figure className="scene-media">
        {media}
        <figcaption title={label}>{label}</figcaption>
      </figure>
    );

  if (attachment.kind === "picture") {
    return labelled(
      <a href={attachment.url} target="_blank" rel="noreferrer">
        <img
          className="picture"
          src={attachment.url}
          alt={`${described} (${attachment.position})`}
          loading="lazy"
        />
      </a>,
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
  if (label === null && role === "agent") {
    return (
      <div className="final-ad" role="group" aria-label="Your ad">
        <div className="final-ad-top">
          <strong>Your ad</strong>
          <a className="final-ad-download" href={attachment.url} download>
            Download
          </a>
        </div>
        <video
          className="video"
          controls
          playsInline
          src={attachment.url}
          aria-label="Video the agent made"
        />
      </div>
    );
  }
  const cover =
    attachment.scene === null
      ? undefined
      : (covers.get(`${attachment.scene} ${attachment.version}`) ??
        covers.get(`${attachment.scene}`));
  return labelled(
    <video
      className="video"
      controls
      playsInline
      preload="metadata"
      poster={cover}
      src={attachment.url}
      aria-label="Video the agent made"
    />,
  );
}

/** The one box the whole app is driven from: free text, and photos when there are any.
 * It stays open while the agent works, so the user is never locked out of answering. */
function Composer({
  text,
  onChange,
  onSend,
  placeholder,
}: {
  text: string;
  onChange: (text: string) => void;
  onSend: (text: string, photos: File[]) => Promise<void>;
  placeholder: string;
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
        placeholder={placeholder}
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
