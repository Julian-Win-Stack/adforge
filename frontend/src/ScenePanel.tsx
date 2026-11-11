import {
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type FormEvent,
} from "react";
import { ApiError, checkClip, getSceneVersions, putVersionInAd } from "./api";
import type { CardVersion, Message, OwnerCheck, Sent } from "./api";
import { CheckIcon, ChevronIcon, CrossIcon, DashIcon, ImageIcon } from "./icons";
import { ATTACHMENT_TYPES, Thumb, pastedPhotos } from "./photos";
import {
  adFinished,
  noVersionPassed,
  notesMessage,
  sceneCards,
  sceneNotes,
  nonBlankNotes,
  sentShown,
  suggestedNotes,
  suggestionShown,
  versionOnShow,
  type ClipSent,
  type SceneCard,
  type TypedNote,
} from "./scenes";

/** The photos the owner may send with one scene's note (Julian, Oct 15). */
const MOST_NOTE_PHOTOS = 3;

/** A photo or video sent with a scene's note, and that scene. */
export type NoteAttachment = { file: File; scene: number };

/** A chat notice asks the panel to open this exact clip; each tap is a new request. */
export type WatchClip = { scene: number; clip: number; request: number };

/** Every scene of the ad so far, one card each: the clip in the ad, every other clip it was
 * shown to watch and swap in, or its starting picture until the clip is made, and what is
 * being made for it now. Built from the messages the chat already has, so it shows nothing
 * until a scene has something; each scene's clips are asked for again as messages come. Each
 * card's note can carry photos or videos, sent with the notes, each with its scene. */
export function ScenePanel({
  sessionId,
  messages,
  onSend,
  watch = null,
  onChecked,
}: {
  sessionId: string | null;
  messages: Message[];
  watch?: WatchClip | null;
  onChecked?: (clip: number, choice: OwnerCheck, sent: Sent | null) => void;
  onSend: (text: string, attachments: NoteAttachment[]) => Promise<void>;
}) {
  const cards = useMemo(() => sceneCards(messages), [messages]);
  const [versions, setVersions] = useState<ReadonlyMap<number, CardVersion[]>>(new Map());
  const lastSeq = messages.at(-1)?.seq ?? 0;
  const anyCard = cards.length > 0;
  // Counts what was asked of the server about the clips, so an answer to an older question,
  // as a fetch a swap overtook, never undoes a newer one.
  const asked = useRef(0);
  useEffect(() => {
    if (sessionId === null || !anyCard) return;
    let current = true;
    const question = ++asked.current;
    let retry: ReturnType<typeof setTimeout>;
    function load() {
      getSceneVersions(sessionId as string).then(
        (found) => {
          if (!current || question !== asked.current) return;
          setVersions(new Map(found.map(({ scene, versions }) => [scene, versions])));
          // The judge posts its notice before the step finishes publishing the clip.
          // Keep asking until that exact clip reaches the card's versions.
          if (
            watch &&
            !found
              .find((one) => one.scene === watch.scene)
              ?.versions.some((one) => one.clip === watch.clip)
          ) {
            retry = setTimeout(load, 2000);
          }
        },
        // The chat's poll already reports a lost connection. A requested clip keeps trying.
        () => {
          if (current && watch) retry = setTimeout(load, 2000);
        },
      );
    }
    load();
    return () => {
      current = false;
      clearTimeout(retry);
    };
  }, [sessionId, lastSeq, anyCard, watch]);
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
  // The notes live here, not in the cards, so folding the cards away keeps them.
  const [typed, setTyped] = useState<ReadonlyMap<number, TypedNote>>(new Map());
  // A suggested note fills a scene's box until the owner types in it, clearing it included.
  const suggested = useMemo(() => suggestedNotes(messages), [messages]);
  const notes = sceneNotes(suggested, typed);
  const fromSuggestion = suggestionShown(suggested, typed);
  // Kept beside the notes, so folding the cards away keeps them too.
  const [attachments, setAttachments] = useState<ReadonlyMap<number, File[]>>(new Map());
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  if (cards.length === 0) return null;
  const busy = cards.filter((card) => card.busy).length;
  const message = notesMessage(notes);
  const noted = nonBlankNotes(notes).length;
  // A file goes only with words saying what to take from it.
  const wordless = [...attachments]
    .filter(([scene, added]) => added.length > 0 && !notes.get(scene)?.trim())
    .sort(([a], [b]) => a - b);
  const sendable = noted > 0 && wordless.length === 0;

  function addAttachments(scene: number, files: Iterable<File>) {
    setAttachments((had) => {
      const have = had.get(scene) ?? [];
      // Every pasted screenshot is named alike, so only the very same file is one already in.
      const fresh = [...new Set(files)].filter((file) => !have.includes(file));
      let photoCount = have.filter((file) => !file.type.startsWith("video/")).length;
      const accepted = fresh.filter(
        (file) => file.type.startsWith("video/") || ++photoCount <= MOST_NOTE_PHOTOS,
      );
      return new Map(had).set(scene, [...have, ...accepted]);
    });
  }

  function removeAttachment(scene: number, index: number) {
    setAttachments((had) =>
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
        [...attachments]
          .sort(([a], [b]) => a - b)
          .flatMap(([scene, added]) => added.map((file) => ({ file, scene }))),
      );
      setTyped(new Map());
      setAttachments(new Map());
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Couldn't reach the server. Try again.");
    } finally {
      setSending(false);
    }
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
        <form className="scene-form" onSubmit={submit} onFocus={() => choose(true)}>
          <ol className="scene-cards">
            {cards.map((card) => (
              <li key={card.scene}>
                <SceneCardView
                  card={card}
                  watch={watch?.scene === card.scene ? watch : null}
                  versions={versions.get(card.scene) ?? []}
                  onCheck={async (clip, choice, note) => {
                    ++asked.current;
                    const after = await checkClip(
                      sessionId as string,
                      card.scene,
                      clip,
                      choice,
                      note,
                    );
                    setVersions((had) => new Map(had).set(after.scene, after.versions));
                    onChecked?.(clip, choice, after.sent);
                  }}
                  onUse={async (version) => {
                    ++asked.current;
                    const after = await putVersionInAd(sessionId as string, card.scene, version);
                    setVersions((had) => new Map(had).set(after.scene, after.versions));
                  }}
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
                  attachments={attachments.get(card.scene) ?? []}
                  onAddAttachments={(files) => addAttachments(card.scene, files)}
                  onRemoveAttachment={(index) => removeAttachment(card.scene, index)}
                />
              </li>
            ))}
          </ol>
          <div className="scene-send">
            {error && (
              <p role="alert" className="notice info inline">
                {error}
              </p>
            )}
            {noted === 0 && wordless.length === 0 && (
              <p className="scene-send-hint">Write a note on any scene, then send them together.</p>
            )}
            {wordless.length > 0 && (
              <p className="scene-send-hint">
                Say in scene {wordless[0][0]}'s note what to take from its{" "}
                {wordless[0][1].some((file) => file.type.startsWith("video/"))
                  ? "attachment"
                  : "photo"}
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

/** One scene: the clip in the ad plays here, with a strip of every clip the scene was shown
 * under it. A tap on one previews it in the player, to put it in the ad or go back; a swap
 * can be undone. While a new clip is being made the old one stays, marked. */
function SceneCardView({
  card,
  watch,
  versions,
  onUse,
  onCheck,
  note,
  suggested,
  onNote,
  attachments,
  onAddAttachments,
  onRemoveAttachment,
}: {
  card: SceneCard;
  watch: WatchClip | null;
  /** The scene's clips as the server has them now: empty until it has said. */
  versions: CardVersion[];
  /** Put a version in the ad; rejects with the server's reason when it can't. */
  onUse: (version: number) => Promise<void>;
  onCheck: (clip: number, choice: OwnerCheck, note: string) => Promise<void>;
  note: string;
  /** The note is the producer's suggestion, untouched by the owner. */
  suggested: boolean;
  onNote: (note: string) => void;
} & NoteFiles) {
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
  const [checking, setChecking] = useState(false);
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
  const sent = onShow ? sentShown(onShow.sent) : (latest?.sent ?? null);
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

  async function check(choice: OwnerCheck) {
    if (checking || onShow?.clip === undefined) return;
    setChecking(true);
    setError(null);
    try {
      await onCheck(onShow.clip, choice, choice === "wrong" ? note : "");
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Couldn't reach the server. Try again.");
    } finally {
      setChecking(false);
    }
  }

  return (
    <article
      ref={element}
      tabIndex={-1}
      className={`scene-card${card.busy ? " busy" : ""}`}
      aria-label={name}
    >
      <div className={`scene-media${preview ? " previewing" : ""}`}>
        {clip ? (
          <video
            key={clip.url}
            // With no first frame to show, the browser shows the clip's own, a moment in.
            src={clip.poster ? clip.url : `${clip.url}#t=0.1`}
            poster={clip.poster ?? undefined}
            controls
            playsInline
            autoPlay={preview !== null}
            preload="metadata"
            aria-label={preview ? `${name}'s v${preview.number}` : `${name}'s clip`}
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
      <div className="scene-foot">
        <span className="scene-name">{name}</span>
        {version && (
          <span className="scene-version-label" title={version}>
            {version}
          </span>
        )}
      </div>
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
      ) : onShow?.clip !== undefined &&
        (onShow.verdict === "unsure" ||
          (onShow.verdict === "not_checked" && watch?.clip === onShow.clip)) ? (
        <div className="scene-owner-check" role="group" aria-label="Check this clip">
          <button type="button" disabled={checking} onClick={() => check("right")}>
            Looks right
          </button>
          <button type="button" disabled={checking} onClick={() => check("wrong")}>
            Looks wrong
          </button>
        </div>
      ) : null}
      {error && (
        <p role="alert" className="notice info inline">
          {error}
        </p>
      )}
      {sent && <SentView sent={sent} />}
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
        attachments={attachments}
        onAddAttachments={onAddAttachments}
        onRemoveAttachment={onRemoveAttachment}
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
 * order they went, the video pieces, and in plain text what didn't go as planned. Where the card is
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
        placeholder="Add a note"
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
