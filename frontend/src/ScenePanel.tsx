import { useMemo, useState, type FormEvent } from "react";
import { ApiError } from "./api";
import type { Message } from "./api";
import { ChevronIcon } from "./icons";
import { notesMessage, sceneCards, nonBlankNotes, type SceneCard } from "./scenes";

/** Every scene of the ad so far, one card each: its latest clip, or its starting picture
 * until the clip is made, and what is being made for it now. Built from the messages the
 * chat already has, so it shows nothing until a scene has something. */
export function ScenePanel({
  messages,
  onSend,
}: {
  messages: Message[];
  onSend: (text: string) => Promise<void>;
}) {
  const cards = useMemo(() => sceneCards(messages), [messages]);
  const [open, setOpen] = useState(true);
  // The notes live here, not in the cards, so folding the cards away keeps them.
  const [notes, setNotes] = useState<ReadonlyMap<number, string>>(new Map());
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  if (cards.length === 0) return null;
  const busy = cards.filter((card) => card.busy).length;
  const message = notesMessage(notes);
  const noted = nonBlankNotes(notes).length;

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (sending) return;
    setSending(true);
    setError(null);
    try {
      await onSend(message);
      setNotes(new Map());
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Couldn't reach the server. Try again.");
    } finally {
      setSending(false);
    }
  }

  return (
    <section className={`scene-panel ${open ? "open" : "closed"}`} aria-label="Scenes">
      <button
        className="scene-panel-toggle"
        aria-expanded={open}
        onClick={() => setOpen((was) => !was)}
      >
        <span className="scene-panel-title">Scenes</span>
        <span className="scene-panel-count">{cards.length}</span>
        {busy > 0 && (
          <span className="scene-panel-busy">
            <i className="pulse" />
            {busy} being made
          </span>
        )}
        <ChevronIcon />
      </button>
      {open && (
        <form className="scene-form" onSubmit={submit}>
          <ol className="scene-cards">
            {cards.map((card) => (
              <li key={card.scene}>
                <SceneCardView
                  card={card}
                  note={notes.get(card.scene) ?? ""}
                  onNote={(note) => setNotes((had) => new Map(had).set(card.scene, note))}
                />
              </li>
            ))}
          </ol>
          <div className="scene-send">
            {error && (
              <p role="alert" className="notice error inline">
                {error}
              </p>
            )}
            {noted === 0 && (
              <p className="scene-send-hint">Write a note on any scene, then send them together.</p>
            )}
            <button type="submit" className="scene-send-button" disabled={sending || noted === 0}>
              {noted === 0 ? "Send notes" : `Send ${noted} note${noted === 1 ? "" : "s"}`}
            </button>
          </div>
        </form>
      )}
    </section>
  );
}

const STEP_NOUN = { clip: "clip", starting_picture: "picture" } as const;

/** One scene: the newest clip plays here, the one before it is a tap away, and while a
 * new one is being made the old one stays, marked. */
function SceneCardView({
  card,
  note,
  onNote,
}: {
  card: SceneCard;
  note: string;
  onNote: (note: string) => void;
}) {
  // Which clip count the owner tapped "Previous" at: a new clip landing brings the card
  // back to the newest one, without remounting the card and losing a note being typed.
  const [previousAt, setPreviousAt] = useState<number | null>(null);
  const showPrevious = previousAt === card.clips.length;
  const latest = card.clips.at(-1) ?? null;
  const previous = card.clips.at(-2) ?? null;
  const onPrevious = showPrevious && previous !== null;
  const clip = onPrevious ? previous : latest;
  const name = `Scene ${card.scene}`;

  let status: string | null = null;
  let tone = "";
  if (card.beingRemade) {
    status = "Being remade";
  } else if (card.making) {
    status = `${card.making.remaking ? "Remaking" : "Making"} the ${STEP_NOUN[card.making.kind]}`;
  } else if (card.failed) {
    status = `Couldn't make the ${STEP_NOUN[card.failed]}`;
    tone = " failed";
  } else if (card.clipOutOfDate) {
    status = "Waiting to finish";
    tone = " waiting";
  }

  return (
    <article className={`scene-card${card.busy ? " busy" : ""}`} aria-label={name}>
      <div className="scene-media">
        {clip ? (
          <video
            key={clip.file.url}
            src={clip.file.url}
            poster={clip.picture?.url}
            controls
            playsInline
            preload="metadata"
            aria-label={onPrevious ? `${name}'s previous clip` : `${name}'s clip`}
          />
        ) : card.picture ? (
          <a href={card.picture.url} target="_blank" rel="noreferrer">
            <img src={card.picture.url} alt={`${name}'s starting picture`} loading="lazy" />
          </a>
        ) : (
          <div className="scene-empty" />
        )}
        {status && (
          <span className={`scene-status${tone}`}>
            {card.busy && <i className="pulse" />}
            {status}
          </span>
        )}
        {onPrevious && <span className="scene-version">Previous clip</span>}
        {clip && card.clipOutOfDate && card.picture && (
          <img
            className="scene-inset"
            src={card.picture.url}
            alt={`${name}'s new starting picture`}
          />
        )}
      </div>
      <div className="scene-foot">
        <span className="scene-name">{name}</span>
        {previous && (
          <button
            className="scene-swap"
            aria-pressed={onPrevious}
            onClick={() => setPreviousAt(onPrevious ? null : card.clips.length)}
          >
            {onPrevious ? "Latest" : "Previous"}
          </button>
        )}
      </div>
      <textarea
        className="scene-note"
        aria-label={`Note for ${name.toLowerCase()}`}
        placeholder="Add a note"
        value={note}
        onChange={(event) => onNote(event.target.value)}
        rows={2}
      />
    </article>
  );
}
