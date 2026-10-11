import { useMemo, useState } from "react";
import type { Message } from "./api";
import { ChevronIcon } from "./icons";
import { sceneCards, type SceneCard } from "./scenes";

/** Every scene of the ad so far, one card each: its latest clip, or its starting picture
 * until the clip is made, and what is being made for it now. Built from the messages the
 * chat already has, so it shows nothing until a scene has something. */
export function ScenePanel({ messages }: { messages: Message[] }) {
  const cards = useMemo(() => sceneCards(messages), [messages]);
  const [open, setOpen] = useState(true);
  if (cards.length === 0) return null;
  const busy = cards.filter((card) => card.making !== null).length;

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
        <ol className="scene-cards">
          {cards.map((card) => (
            <li key={card.scene}>
              <SceneCardView card={card} />
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}

const THING = { clip: "clip", starting_picture: "picture" } as const;

/** One scene: the newest clip plays here, the one before it is a tap away, and while a
 * new one is being made the old one stays, marked. */
function SceneCardView({ card }: { card: SceneCard }) {
  const [showPrevious, setShowPrevious] = useState(false);
  const latest = card.clips.at(-1) ?? null;
  const previous = card.clips.at(-2) ?? null;
  const onPrevious = showPrevious && previous !== null;
  const clip = onPrevious ? previous : latest;
  const name = `Scene ${card.scene}`;

  let status: string | null = null;
  if (card.making) {
    status = `${card.making.remaking ? "Remaking" : "Making"} the ${THING[card.making.kind]}`;
  } else if (card.failed) {
    status = `Couldn't make the ${THING[card.failed]}`;
  }

  return (
    <article className={`scene-card${card.making ? " busy" : ""}`} aria-label={name}>
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
          <span className={`scene-status${card.making ? "" : " failed"}`}>
            {card.making && <i className="pulse" />}
            {status}
          </span>
        )}
        {onPrevious && <span className="scene-version">Previous clip</span>}
      </div>
      <div className="scene-foot">
        <span className="scene-name">{name}</span>
        {previous && (
          <button
            className="scene-swap"
            aria-pressed={onPrevious}
            onClick={() => setShowPrevious((was) => !was)}
          >
            {onPrevious ? "Latest" : "Previous"}
          </button>
        )}
      </div>
    </article>
  );
}
