import { useLayoutEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { ApiError } from "./api";
import type { Message } from "./api";
import { ChevronIcon } from "./icons";
import {
  adFinished,
  notesMessage,
  sceneCards,
  sceneNotes,
  nonBlankNotes,
  suggestedNotes,
  suggestionShown,
  type SceneCard,
  type TypedNote,
} from "./scenes";

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
  // Until the owner folds or opens the cards, they are open, except on a phone once the ad
  // is finished: there they would take half the screen from the chat and the ad.
  const [chosen, setChosen] = useState<boolean | null>(null);
  const open = chosen ?? !(isPhone() && adFinished(messages));
  // The notes live here, not in the cards, so folding the cards away keeps them.
  const [typed, setTyped] = useState<ReadonlyMap<number, TypedNote>>(new Map());
  // A suggested note fills a scene's box until the owner types in it, clearing it included.
  const suggested = useMemo(() => suggestedNotes(messages), [messages]);
  const notes = sceneNotes(suggested, typed);
  const fromSuggestion = suggestionShown(suggested, typed);
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
      setTyped(new Map());
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Couldn't reach the server. Try again.");
    } finally {
      setSending(false);
    }
  }

  return (
    <section className={`scene-panel ${open ? "open" : "closed"}`} aria-label="Scenes">
      <button className="scene-panel-toggle" aria-expanded={open} onClick={() => setChosen(!open)}>
        <span className="scene-panel-title">Scenes</span>
        <span className="scene-panel-count">{cards.length}</span>
        {!open && (
          <span className="scene-thumbs" aria-hidden="true">
            {cards.map((card) =>
              card.picture ? (
                <img key={card.scene} src={card.picture.url} alt="" />
              ) : (
                <i key={card.scene} />
              ),
            )}
          </span>
        )}
        {busy > 0 && (
          <span className="scene-panel-busy">
            <i className="pulse" />
            {busy} being made
          </span>
        )}
        <ChevronIcon />
      </button>
      {open && (
        // Once the owner is in the cards, the ad landing doesn't fold them away under them.
        <form className="scene-form" onSubmit={submit} onFocus={() => setChosen(true)}>
          <ol className="scene-cards">
            {cards.map((card) => (
              <li key={card.scene}>
                <SceneCardView
                  card={card}
                  note={notes.get(card.scene) ?? ""}
                  suggested={fromSuggestion.has(card.scene)}
                  onNote={(note) =>
                    setTyped((had) =>
                      new Map(had).set(card.scene, {
                        text: note,
                        over: suggested.get(card.scene)?.seq ?? null,
                      }),
                    )
                  }
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

/** A phone-sized window, where the cards sit above the chat and share its little height. */
function isPhone() {
  return window.innerWidth <= 720;
}

const STEP_NOUN = { clip: "clip", starting_picture: "picture" } as const;

/** One scene: the newest clip plays here, the one before it is a tap away, and while a
 * new one is being made the old one stays, marked. */
function SceneCardView({
  card,
  note,
  suggested,
  onNote,
}: {
  card: SceneCard;
  note: string;
  /** The note is the producer's suggestion, untouched by the owner. */
  suggested: boolean;
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
  // Which version the card shows, as the owner and the fix plan name it: a new picture
  // waiting for its clip is the newest one, shown beside the clip it replaces.
  const newest = clip && !(card.clipOutOfDate && !onPrevious) ? clip.file : card.picture;
  const version = newest?.version || null;

  let status: string | null = null;
  let tone = "";
  if (card.beingRemade) {
    status = "Being remade";
  } else if (card.making) {
    status = `${card.making.remaking ? "Remaking" : "Making"} the ${STEP_NOUN[card.making.kind]}`;
  } else if (card.failed === "clip") {
    // No badge: the red chat notice already tells the owner the clip couldn't be made.
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
        {version && (
          <span className="scene-version-label" title={version}>
            {version}
          </span>
        )}
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
      {suggested && (
        <span
          className="scene-note-suggested"
          title="The producer suggested this note. Change it or clear it before sending."
        >
          Suggested
        </span>
      )}
      <NoteBox name={name} note={note} onNote={onNote} />
    </article>
  );
}

/** A scene's note box, as tall as what is written in it, so a long note shows whole. */
function NoteBox({
  name,
  note,
  onNote,
}: {
  name: string;
  note: string;
  onNote: (note: string) => void;
}) {
  const box = useRef<HTMLTextAreaElement>(null);
  useLayoutEffect(() => {
    const element = box.current;
    if (element === null) return;
    // Measured at its smallest, so deleting lines shrinks it again.
    element.style.setProperty("--note-height", "0px");
    const borders = element.offsetHeight - element.clientHeight;
    element.style.setProperty("--note-height", `${element.scrollHeight + borders}px`);
  }, [note]);
  return (
    <textarea
      ref={box}
      className="scene-note"
      aria-label={`Note for ${name.toLowerCase()}`}
      placeholder="Add a note"
      value={note}
      onChange={(event) => onNote(event.target.value)}
      rows={2}
    />
  );
}
