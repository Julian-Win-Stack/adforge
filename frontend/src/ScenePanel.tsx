import { useLayoutEffect, useRef, useState, type CSSProperties, type FormEvent } from "react";
import { ApiError } from "./api";
import type { CardVersion, Message } from "./api";
import { CheckIcon, ChevronIcon, CrossIcon, DashIcon, ImageIcon } from "./icons";
import { ATTACHMENT_TYPES, Thumb, pastedPhotos } from "./photos";
import { SceneClipPlayer } from "./SceneClipPlayer";
import { adFinished, noVersionPassed, versionOnShow, type SceneCard } from "./scenes";
import {
  MOST_NOTE_PHOTOS,
  fixLabel,
  wordlessHint,
  type SceneWork,
  type WatchClip,
} from "./sceneWork";

const EMPTY: CardVersion[] = [];

export type { NoteAttachment, WatchClip } from "./sceneWork";

/** Every scene of the ad so far, one card each, while the ad or a scene is being made: the
 * clip in the ad, every other clip it was shown to watch and swap in, or its starting
 * picture until the clip is made, and what is being made for it now. Built from the messages
 * the chat already has, so it shows nothing until a scene has something. Each card's note can
 * carry photos or videos, sent with the notes, each with its scene. The notes and clips are
 * the chat's `work`, shared with the scene list under the finished ad. */
export function ScenePanel({
  messages,
  work,
  watch = null,
}: {
  messages: Message[];
  work: SceneWork;
  watch?: WatchClip | null;
}) {
  const { cards } = work;
  // Until the owner folds or opens the cards, they are open, except on a phone once the ad
  // is finished: there they would take half the screen from the chat and the ad.
  const [chosen, setChosen] = useState<{ open: boolean; request?: number } | null>(null);
  const open =
    watch && chosen?.request !== watch.request
      ? true
      : (chosen?.open ?? !(isPhone() && adFinished(messages)));
  const choose = (open: boolean) => setChosen({ open, request: watch?.request });
  const [drag, setDrag] = useState<{
    pointer: number;
    start: number;
    distance: number;
  } | null>(null);
  if (cards.length === 0) return null;
  const busy = cards.filter((card) => card.busy).length;
  const hint = wordlessHint(work.wordless);

  function submit(event: FormEvent) {
    event.preventDefault();
    void work.send();
  }

  return (
    <section
      className={`scene-panel ${open ? "open" : "closed"}${drag ? " dragging" : ""}`}
      style={drag ? ({ "--scene-panel-drag": `${drag.distance}px` } as CSSProperties) : undefined}
      aria-label="Scenes"
      onKeyDown={(event) => {
        if (event.key === "Escape") setDrag(null);
      }}
    >
      {open && (
        <button
          type="button"
          className="scene-panel-drag-handle"
          aria-label="Close scenes"
          title="Drag right to close scenes"
          onPointerDown={(event) => {
            // Below this breakpoint the panel is a horizontal strip above the chat.
            if (window.innerWidth <= 1180 || event.button !== 0 || !event.isPrimary) return;
            event.preventDefault();
            event.currentTarget.focus({ preventScroll: true });
            event.currentTarget.setPointerCapture(event.pointerId);
            setDrag({ pointer: event.pointerId, start: event.clientX, distance: 0 });
          }}
          onPointerMove={(event) => {
            if (!drag || drag.pointer !== event.pointerId) return;
            setDrag({ ...drag, distance: Math.max(0, Math.min(260, event.clientX - drag.start)) });
          }}
          onPointerUp={(event) => {
            if (!drag || drag.pointer !== event.pointerId) return;
            if (window.innerWidth > 1180 && event.clientX - drag.start >= 80) choose(false);
            setDrag(null);
          }}
          onPointerCancel={() => setDrag(null)}
          onLostPointerCapture={() => setDrag(null)}
          onClick={(event) => {
            // Keyboard activation closes it too; a click after a short drag leaves it open.
            if (event.detail === 0) choose(false);
          }}
        />
      )}
      <button className="scene-panel-toggle" aria-expanded={open} onClick={() => choose(!open)}>
        <span className="scene-panel-title">Your ad, scene by scene</span>
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
        <p className="scene-panel-sub">Something off? Write what to change under that scene.</p>
      )}
      {open && (
        // Once the owner is in the cards, the ad landing doesn't fold them away under them.
        <form className="scene-form" onSubmit={submit} onFocus={() => choose(true)}>
          <ol className="scene-cards">
            {cards.map((card) => (
              <li key={card.scene}>
                <SceneCardView
                  card={card}
                  watch={watch?.scene === card.scene ? watch : null}
                  work={work}
                />
              </li>
            ))}
          </ol>
          <div className="scene-send">
            {work.error && (
              <p role="alert" className="notice info inline">
                {work.error}
              </p>
            )}
            {hint && <p className="scene-send-hint">{hint}</p>}
            <button
              type="submit"
              className="scene-send-button"
              disabled={work.sending || !work.sendable}
            >
              {fixLabel(work.noted.length)}
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

/** One scene: the clip in the ad plays here, with a strip of every clip the scene was shown
 * under it. A tap on one previews it in the player, to put it in the ad or go back; a swap
 * can be undone. While a new clip is being made the old one stays, marked. */
export function SceneCardView({
  card,
  watch,
  work,
  layout = "card",
}: {
  card: SceneCard;
  watch: WatchClip | null;
  work: SceneWork;
  /** A card in the panel shows its clip and name; an opened row under the finished ad,
   * whose ad plays above it and whose row names it, shows a clip only to preview one. */
  layout?: "card" | "row";
}) {
  const versions = work.versions.get(card.scene) ?? EMPTY;
  const onUse = (version: number) => work.putInAd(card.scene, version);
  const note = work.notes.get(card.scene) ?? "";
  const onNote = (text: string) => work.setNote(card.scene, text);
  const [picked, setPicked] = useState<{ number: number | null; request?: number } | null>(null);
  const requested = watch !== null && picked?.request !== watch.request;
  const watchedVersion = watch ? versions.find((one) => one.clip === watch.clip) : undefined;
  const waitingForClip = requested && watchedVersion === undefined;
  const previewing = requested ? (watchedVersion?.number ?? null) : (picked?.number ?? null);
  const element = useRef<HTMLElement>(null);
  useLayoutEffect(() => {
    if (!watch) return;
    element.current?.scrollIntoView?.({ block: "nearest", behavior: "smooth" });
    element.current?.focus({ preventScroll: true });
  }, [watch]);
  // The last swap, until the owner does anything else, so it can be undone.
  // `from` is null when no clip was in the ad before it, so there is nothing to undo to.
  const [swapped, setSwapped] = useState<{ to: number; from: number | null } | null>(null);
  const [using, setUsing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const onShow = requested ? (watchedVersion ?? null) : versionOnShow(versions, previewing);
  const inAd = versions.find((one) => one.in_ad) ?? null;
  const preview = onShow !== null && !onShow.in_ad && onShow.number === previewing ? onShow : null;
  const latest = waitingForClip ? null : (card.clips.at(-1) ?? null);
  // The chat's newest clip until the server has said which is in the ad.
  const clip = onShow
    ? { url: onShow.url, poster: onShow.picture }
    : latest && {
        url: latest.file.url,
        poster: latest.picture?.url ?? latest.sent?.pictures[0]?.url ?? null,
      };
  const name = `Scene ${card.scene}`;
  // Which version the card shows, as the owner and the producer name it: a new picture
  // waiting for its clip is the newest one, shown beside the clip it replaces.
  const version =
    (card.clipOutOfDate && !preview ? card.picture?.version : onShow?.label) ??
    latest?.file.version ??
    card.picture?.version ??
    null;
  // Said until the owner does anything else, or another clip is in the ad: none is when the
  // swapped-in clip was of a scene said to camera, which the scene now shows again.
  const confirmed =
    swapped !== null && (inAd === null || inAd.number === swapped.to) ? swapped : null;

  let status: string | null = null;
  let tone = "";
  if (card.beingRemade) {
    status = "Being remade";
  } else if (card.making) {
    status = `${card.making.remaking ? "Remaking" : "Making"} the ${STEP_NOUN[card.making.kind]}`;
  } else if (card.failed === "clip") {
    // No badge: the chat notice already tells the owner the clip couldn't be made.
  } else if (card.failed) {
    status = `Couldn't make the ${STEP_NOUN[card.failed]}`;
    tone = " failed";
  } else if (card.clipOutOfDate) {
    status = "Waiting to finish";
    tone = " waiting";
  }

  function pick(number: number | null) {
    setPicked({ number, request: watch?.request });
    setSwapped(null);
    setError(null);
  }

  async function use(number: number, then: { to: number; from: number | null } | null) {
    if (using) return;
    setUsing(true);
    setError(null);
    try {
      await onUse(number);
      setPicked({ number: null, request: watch?.request });
      setSwapped(then);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Couldn't reach the server. Try again.");
    } finally {
      setUsing(false);
    }
  }

  return (
    <article
      ref={element}
      tabIndex={-1}
      className={`scene-card${card.busy ? " busy" : ""}${layout === "row" ? " in-row" : ""}`}
      aria-label={name}
    >
      {(layout === "card" || preview || waitingForClip) && (
        <div className={`scene-media${preview ? " previewing" : ""}`}>
          {clip ? (
            <SceneClipPlayer
              key={clip.url}
              url={clip.url}
              poster={clip.poster}
              captions={onShow?.captions ?? []}
              scene={card.scene}
              autoPlay={preview !== null}
              label={preview ? `${name}'s v${preview.number}` : `${name}'s clip`}
            />
          ) : waitingForClip ? (
            <p className="scene-empty" role="status">
              Loading this clip…
            </p>
          ) : card.picture ? (
            <a href={card.picture.url} target="_blank" rel="noreferrer">
              <img src={card.picture.url} alt={`${name}'s starting picture`} loading="lazy" />
            </a>
          ) : (
            <div className="scene-empty" />
          )}
          {status && !preview && (
            <span className={`scene-status${tone}`}>
              {card.busy && <i className="pulse" />}
              {status}
            </span>
          )}
          {preview && <span className="scene-status previewing">Previewing v{preview.number}</span>}
          {clip && card.clipOutOfDate && card.picture && !preview && (
            <img
              className="scene-inset"
              src={card.picture.url}
              alt={`${name}'s new starting picture`}
            />
          )}
        </div>
      )}
      {versions.length > 1 && (
        <ol className="scene-strip" aria-label={`${name}'s clips`}>
          {versions.map((one) => (
            <li key={one.number}>
              <VersionThumb
                version={one}
                current={inAd}
                previewing={preview?.number === one.number}
                onPick={() => pick(one.in_ad ? null : one.number)}
              />
            </li>
          ))}
        </ol>
      )}
      {layout === "card" && (
        <div className="scene-foot">
          <span className="scene-name">{name}</span>
          {version && (versions.length > 1 || card.clips.length > 1) && (
            <span className="scene-version-label" title={version}>
              {version}
            </span>
          )}
        </div>
      )}
      {preview ? (
        <div className="scene-preview" role="group" aria-label={`Previewing v${preview.number}`}>
          <CheckLine key={preview.number} version={preview} />
          {(preview.line !== inAd?.line || preview.shows !== inAd?.shows) && (
            <p className="scene-version-words">
              {preview.line && <span>Says: “{preview.line}”</span>}{" "}
              {preview.shows && <span>Shows: {preview.shows}</span>}
            </p>
          )}
          {preview.free ? (
            <button
              type="button"
              className="scene-use"
              disabled={using || card.busy}
              title={card.busy ? "Wait until the new clip is made." : undefined}
              onClick={() =>
                use(preview.number, { to: preview.number, from: inAd?.number ?? null })
              }
            >
              {using ? "Putting it in…" : "Use this in the ad"}
            </button>
          ) : (
            <div className="scene-preview-old">
              <p>
                {preview.refilm || "This version needs filming again before it can go in the ad."}
              </p>
              <button
                type="button"
                className="scene-use"
                disabled={card.busy}
                onClick={() => {
                  onNote(`Please refilm v${preview.number} of scene ${card.scene}.`);
                  pick(null);
                }}
              >
                Ask to refilm this version
              </button>
            </div>
          )}
          <button type="button" className="scene-back" onClick={() => pick(null)}>
            Back
          </button>
        </div>
      ) : confirmed ? (
        <p className="scene-swapped" role="status">
          <span>v{confirmed.to} is in the ad now</span>
          {confirmed.from !== null && (
            <button
              type="button"
              className="scene-undo"
              disabled={using}
              onClick={() => use(confirmed.from as number, null)}
            >
              {using ? "Undoing…" : "Undo"}
            </button>
          )}
        </p>
      ) : (
        noVersionPassed(versions) &&
        !card.busy && (
          <p className="scene-none-passed" role="note">
            No version passed the check. Pick the best one.
          </p>
        )
      )}
      {onShow?.owner_check ? (
        <p className="scene-owner-check" role="status">
          You said it looks {onShow.owner_check}.
        </p>
      ) : null}
      {error && (
        <p role="alert" className="notice info inline">
          {error}
        </p>
      )}
      {work.suggested.has(card.scene) && (
        <span
          className="scene-note-suggested"
          title="The agent suggested this note. Change it or clear it before sending."
        >
          Suggested
        </span>
      )}
      <NoteBox
        name={name}
        note={note}
        onNote={onNote}
        attachments={work.attachments.get(card.scene) ?? []}
        onAddAttachments={(files) => work.addAttachments(card.scene, files)}
        onRemoveAttachment={(index) => work.removeAttachment(card.scene, index)}
      />
    </article>
  );
}

const CHECK_WORDS = {
  pass: "passed the check",
  fail: "failed the check",
  unsure: "the check couldn't tell",
  not_checked: "not checked",
} as const;

/** What the check said of a previewed clip, cut short where the card is narrow: a tap shows
 * it whole. */
function CheckLine({ version }: { version: CardVersion }) {
  const [whole, setWhole] = useState(false);
  const said = checkSaid(version);
  return (
    <button
      type="button"
      className={`scene-preview-check ${version.verdict}${whole ? " whole" : ""}`}
      aria-expanded={version.problem ? whole : undefined}
      title={said}
      onClick={() => setWhole(!whole)}
    >
      {said}
    </button>
  );
}

/** What the clip judge said of a clip, in a line: "Failed the check: the mug floats". */
function checkSaid(version: CardVersion) {
  const said = CHECK_WORDS[version.verdict];
  const line = `${said[0].toUpperCase()}${said.slice(1)}`;
  return version.problem ? `${line}: ${version.problem}` : `${line}.`;
}

/** One clip in a card's strip: its first frame, its number, and a badge for what the check
 * said. The one in the ad is outlined; a changed voice or presenter needs refilming. */
function VersionThumb({
  version,
  current,
  previewing,
  onPick,
}: {
  version: CardVersion;
  current: CardVersion | null;
  previewing: boolean;
  onPick: () => void;
}) {
  const old = !version.in_ad && !version.free;
  const line = version.line !== current?.line ? version.line : null;
  const shows = version.shows !== current?.shows ? version.shows : null;
  const state = [
    CHECK_WORDS[version.verdict],
    version.in_ad ? "in the ad" : old ? "needs refilming" : null,
  ]
    .filter(Boolean)
    .join(", ");
  return (
    <button
      type="button"
      className={`scene-thumb${version.in_ad ? " in-ad" : ""}${old ? " old" : ""}`}
      aria-pressed={previewing}
      aria-label={`v${version.number}, ${state}`}
      title={`${version.label}: ${state}`}
      onClick={onPick}
    >
      {version.picture ? (
        <img src={version.picture} alt="" loading="lazy" />
      ) : (
        <video src={`${version.url}#t=0.1`} muted playsInline preload="metadata" tabIndex={-1} />
      )}
      <span className={`scene-thumb-badge ${version.verdict}`} aria-hidden="true">
        {version.verdict === "pass" ? (
          <CheckIcon />
        ) : version.verdict === "fail" ? (
          <CrossIcon />
        ) : (
          <DashIcon />
        )}
      </span>
      <span className="scene-thumb-number">
        v{version.number}
        {old && <small>Needs refilm</small>}
      </span>
      {(line || shows) && (
        <span className="scene-thumb-description">
          {shows && <span>Shows: {shows}</span>}
          {line && <span>Says: “{line}”</span>}
        </span>
      )}
    </button>
  );
}

/** The files on a scene's note, and what adds and takes them off. */
type NoteFiles = {
  attachments: File[];
  onAddAttachments: (files: Iterable<File>) => void;
  onRemoveAttachment: (index: number) => void;
};

/** A scene's note box, as tall as what is written in it, so a long note shows whole. A
 * file comes in by picker or drop; photos may also be pasted. Each stays below until sent. */
function NoteBox({
  name,
  note,
  onNote,
  attachments,
  onAddAttachments,
  onRemoveAttachment,
}: {
  name: string;
  note: string;
  onNote: (note: string) => void;
} & NoteFiles) {
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
  const full =
    attachments.filter((file) => !file.type.startsWith("video/")).length >= MOST_NOTE_PHOTOS;
  return (
    <div
      className="scene-note-box"
      onDragOver={(event) => event.preventDefault()}
      onDrop={(event) => {
        event.preventDefault();
        onAddAttachments(Array.from(event.dataTransfer.files));
      }}
    >
      <textarea
        ref={box}
        className="scene-note"
        aria-label={`Note for ${scene}`}
        placeholder="What should change in this scene?"
        value={note}
        onChange={(event) => onNote(event.target.value)}
        onPaste={(event) => {
          const pictures = pastedPhotos(event);
          if (pictures.length > 0) onAddAttachments(pictures);
        }}
        rows={2}
      />
      <input
        ref={picker}
        className="visually-hidden"
        aria-label={`Photos or videos for ${scene}`}
        type="file"
        multiple
        accept={ATTACHMENT_TYPES.join(",")}
        onChange={(event) => {
          onAddAttachments(Array.from(event.target.files ?? []));
          event.target.value = "";
        }}
        tabIndex={-1}
      />
      <button
        type="button"
        className="scene-note-photo"
        aria-label={`Add a photo or video to ${scene}`}
        title={
          full
            ? `Up to ${MOST_NOTE_PHOTOS} photos per scene; you can still add videos`
            : "Add a photo or video"
        }
        // The note keeps its focus, so a card opened wide to write in stays open.
        onMouseDown={(event) => event.preventDefault()}
        onClick={() => picker.current?.click()}
      >
        <ImageIcon />
      </button>
      {attachments.length > 0 && (
        <div className="scene-note-photos">
          {attachments.map((file, index) =>
            file.type.startsWith("video/") ? (
              <span className="chip" key={index}>
                <span>{file.name}</span>
                <button
                  type="button"
                  aria-label={`Remove ${file.name}`}
                  onClick={() => onRemoveAttachment(index)}
                >
                  <CrossIcon />
                </button>
              </span>
            ) : (
              <Thumb
                // Pasted screenshots share a name, so a photo is its place; each thumbnail's
                // picture follows its file, wherever it moves.
                key={index}
                photo={{ name: file.name, file }}
                label={`Photo for ${scene}: ${file.name}`}
                onRemove={() => onRemoveAttachment(index)}
              />
            ),
          )}
        </div>
      )}
    </div>
  );
}
