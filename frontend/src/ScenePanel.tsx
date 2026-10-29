import { useLayoutEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { ApiError } from "./api";
import type { Message } from "./api";
import { ChevronIcon, ImageIcon } from "./icons";
import { PHOTO_TYPES, Thumb, pastedPhotos } from "./photos";
import {
  adFinished,
  notesMessage,
  sceneCards,
  sceneNotes,
  nonBlankNotes,
  suggestedNotes,
  suggestionShown,
  type ClipSent,
  type SceneCard,
  type TypedNote,
} from "./scenes";

/** The photos the owner may send with one scene's note (Julian, Oct 15). */
const MOST_NOTE_PHOTOS = 3;

/** A photo sent with a scene's note, and that scene. */
export type NotePhoto = { file: File; scene: number };

/** Every scene of the ad so far, one card each: its latest clip, or its starting picture
 * until the clip is made, and what is being made for it now. Built from the messages the
 * chat already has, so it shows nothing until a scene has something. Each card's note can
 * carry photos, sent with the notes, each with its scene. */
export function ScenePanel({
  messages,
  onSend,
}: {
  messages: Message[];
  onSend: (text: string, photos: NotePhoto[]) => Promise<void>;
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
  // Kept beside the notes, so folding the cards away keeps them too.
  const [photos, setPhotos] = useState<ReadonlyMap<number, File[]>>(new Map());
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  if (cards.length === 0) return null;
  const busy = cards.filter((card) => card.busy).length;
  const message = notesMessage(notes);
  const noted = nonBlankNotes(notes).length;
  // A photo goes only with words saying what to take from it.
  const wordless = [...photos]
    .filter(([scene, added]) => added.length > 0 && !notes.get(scene)?.trim())
    .sort(([a], [b]) => a - b);
  const sendable = noted > 0 && wordless.length === 0;

  function addPhotos(scene: number, files: Iterable<File>) {
    setPhotos((had) => {
      const have = had.get(scene) ?? [];
      // Every pasted screenshot is named alike, so only the very same file is one already in.
      const fresh = [...new Set(files)].filter((file) => !have.includes(file));
      return new Map(had).set(scene, [...have, ...fresh].slice(0, MOST_NOTE_PHOTOS));
    });
  }

  function removePhoto(scene: number, index: number) {
    setPhotos((had) =>
      new Map(had).set(
        scene,
        (had.get(scene) ?? []).filter((_, i) => i !== index),
      ),
    );
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (sending || !sendable) return;
    setSending(true);
    setError(null);
    try {
      await onSend(
        message,
        [...photos]
          .sort(([a], [b]) => a - b)
          .flatMap(([scene, added]) => added.map((file) => ({ file, scene }))),
      );
      setTyped(new Map());
      setPhotos(new Map());
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
                  photos={photos.get(card.scene) ?? []}
                  onAddPhotos={(files) => addPhotos(card.scene, files)}
                  onRemovePhoto={(index) => removePhoto(card.scene, index)}
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
            {noted === 0 && wordless.length === 0 && (
              <p className="scene-send-hint">Write a note on any scene, then send them together.</p>
            )}
            {wordless.length > 0 && (
              <p className="scene-send-hint">
                Say in scene {wordless[0][0]}'s note what to take from its photo
                {wordless[0][1].length === 1 ? "" : "s"}.
              </p>
            )}
            <button type="submit" className="scene-send-button" disabled={sending || !sendable}>
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
  photos,
  onAddPhotos,
  onRemovePhoto,
}: {
  card: SceneCard;
  note: string;
  /** The note is the producer's suggestion, untouched by the owner. */
  suggested: boolean;
  onNote: (note: string) => void;
} & NotePhotos) {
  // Which clip count the owner tapped "Previous" at: a new clip landing brings the card
  // back to the newest one, without remounting the card and losing a note being typed.
  const [previousAt, setPreviousAt] = useState<number | null>(null);
  const showPrevious = previousAt === card.clips.length;
  const latest = card.clips.at(-1) ?? null;
  const previous = card.clips.at(-2) ?? null;
  const onPrevious = showPrevious && previous !== null;
  const clip = onPrevious ? previous : latest;
  const name = `Scene ${card.scene}`;
  // Which version the card shows, as the owner and the producer name it: a new picture
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
            poster={clip.picture?.url ?? clip.sent?.pictures[0]?.url}
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
      {clip?.sent && <SentView sent={clip.sent} />}
      {card.question && <QuestionView question={card.question} />}
      {suggested && (
        <span
          className="scene-note-suggested"
          title="The producer suggested this note. Change it or clear it before sending."
        >
          Suggested
        </span>
      )}
      <NoteBox
        name={name}
        note={note}
        onNote={onNote}
        photos={photos}
        onAddPhotos={onAddPhotos}
        onRemovePhoto={onRemovePhoto}
      />
    </article>
  );
}

/** The fixing agent's question for the owner, text only: the answer comes through the
 * producer. Where the card is narrow it is cut short, and a tap shows it whole. */
function QuestionView({ question }: { question: string }) {
  const [whole, setWhole] = useState(false);
  return (
    <div className="scene-question" role="note" aria-label="Question for you">
      <button
        type="button"
        className="scene-question-text"
        aria-expanded={whole}
        title={question}
        onClick={() => setWhole(!whole)}
      >
        <span className="scene-question-title">Question for you</span> {question}
      </button>
    </div>
  );
}

/** What the clip on show was made from: the pictures sent, small with their labels in the
 * order they went, the video pieces, and in red what didn't go as planned. Where the card is
 * too narrow for the labels, a tapped picture's label shows under the pictures. */
function SentView({ sent }: { sent: ClipSent }) {
  const [chosen, setChosen] = useState<number | null>(null);
  const chosenPicture = sent.pictures.find(({ image }) => image === chosen);
  const problems = [
    ...(sent.leftOut.length > 0 ? [`Left out: ${sent.leftOut.join(" ")}`] : []),
    ...sent.notRecoloured.map(
      ({ label, colour }) => `Not made ${colour}, sent as it was: ${label}`,
    ),
  ];
  return (
    <div className="scene-sent">
      <ol className="scene-sent-pictures" aria-label="Pictures sent">
        {sent.pictures.map(({ image, label, url }) => (
          <li key={image} title={label}>
            <button
              type="button"
              className="scene-sent-picture"
              aria-pressed={chosen === image}
              onClick={() => setChosen(chosen === image ? null : image)}
            >
              <img src={url} alt={`Image ${image}: ${label}`} loading="lazy" />
            </button>
            <span className="scene-sent-label">{label}</span>
          </li>
        ))}
      </ol>
      {chosenPicture && (
        <p className="scene-sent-chosen">
          Image {chosenPicture.image}: {chosenPicture.label}
        </p>
      )}
      {sent.pieces.length > 0 && (
        <ul className="scene-sent-pieces" aria-label="Video pieces sent">
          {sent.pieces.map(({ number, url, start, end }) => (
            <li key={number}>
              <video
                src={url}
                controls
                muted
                playsInline
                preload="metadata"
                aria-label={`Video ${number}: ${start}–${end} s`}
              />
            </li>
          ))}
        </ul>
      )}
      {problems.length > 0 && <p className="scene-sent-problem">{problems.join(" ")}</p>}
    </div>
  );
}

/** The photos on a scene's note, and what adds and takes them off. */
type NotePhotos = {
  photos: File[];
  onAddPhotos: (files: Iterable<File>) => void;
  onRemovePhoto: (index: number) => void;
};

/** A scene's note box, as tall as what is written in it, so a long note shows whole. A
 * photo comes in by its photo button or a paste, shown small under it until sent. */
function NoteBox({
  name,
  note,
  onNote,
  photos,
  onAddPhotos,
  onRemovePhoto,
}: {
  name: string;
  note: string;
  onNote: (note: string) => void;
} & NotePhotos) {
  const box = useRef<HTMLTextAreaElement>(null);
  const picker = useRef<HTMLInputElement>(null);
  useLayoutEffect(() => {
    const element = box.current;
    if (element === null) return;
    // Measured at its smallest, so deleting lines shrinks it again.
    element.style.setProperty("--note-height", "0px");
    const borders = element.offsetHeight - element.clientHeight;
    element.style.setProperty("--note-height", `${element.scrollHeight + borders}px`);
  }, [note]);
  const scene = name.toLowerCase();
  const full = photos.length >= MOST_NOTE_PHOTOS;
  return (
    <div className="scene-note-box">
      <textarea
        ref={box}
        className="scene-note"
        aria-label={`Note for ${scene}`}
        placeholder="Add a note"
        value={note}
        onChange={(event) => onNote(event.target.value)}
        onPaste={(event) => {
          const pictures = pastedPhotos(event);
          if (pictures.length > 0) onAddPhotos(pictures);
        }}
        rows={2}
      />
      <input
        ref={picker}
        className="visually-hidden"
        aria-label={`Photos for ${scene}`}
        type="file"
        multiple
        accept={PHOTO_TYPES.join(",")}
        onChange={(event) => {
          onAddPhotos(Array.from(event.target.files ?? []));
          event.target.value = "";
        }}
        tabIndex={-1}
      />
      <button
        type="button"
        className="scene-note-photo"
        aria-label={`Add a photo to ${scene}`}
        title={full ? `Up to ${MOST_NOTE_PHOTOS} photos per scene` : "Add a photo"}
        disabled={full}
        // The note keeps its focus, so a card opened wide to write in stays open.
        onMouseDown={(event) => event.preventDefault()}
        onClick={() => picker.current?.click()}
      >
        <ImageIcon />
      </button>
      {photos.length > 0 && (
        <div className="scene-note-photos">
          {photos.map((file, index) => (
            <Thumb
              key={`${file.name}-${file.size}`}
              photo={{ name: file.name, file }}
              label={`Photo for ${scene}: ${file.name}`}
              onRemove={() => onRemovePhoto(index)}
            />
          ))}
        </div>
      )}
    </div>
  );
}
