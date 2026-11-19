import { useEffect, useRef, useState } from "react";
import { getSceneVersions, type Attachment, type CardVersion } from "./api";
import { ImageIcon } from "./icons";
import { notesMessage, type SceneCard } from "./scenes";

/** TRY (tap a scene): the finished ad with its scenes beside it. Tapping a scene, or its part
 * of the time bar, jumps the ad there and plays that scene, and opens its note box. "Fix N
 * scenes" sends every note in one message, each photo tagged with its scene. */
export function FinishedAd({
  attachment,
  sessionId,
  cards,
  latest,
  onSend,
}: {
  attachment: Attachment;
  sessionId: string | null;
  cards: SceneCard[];
  /** Only the newest ad offers fixing; older ones are plain players. */
  latest: boolean;
  onSend: (text: string, photos: File[], photoScenes: number[]) => Promise<void>;
}) {
  const video = useRef<HTMLVideoElement>(null);
  const stopAt = useRef<number | null>(null);
  const [now, setNow] = useState(0);
  const [length, setLength] = useState(0);
  const [selected, setSelected] = useState<number | null>(null);
  const [notes, setNotes] = useState<Map<number, string>>(new Map());
  const [photos, setPhotos] = useState<Map<number, File[]>>(new Map());
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [words, setWords] = useState<Map<number, CardVersion>>(new Map());

  const spans = attachment.cuts ?? [];
  const scenes = spans.length > 0 ? spans.map((span) => span.scene) : cards.map((c) => c.scene);

  useEffect(() => {
    if (!latest || sessionId === null) return;
    getSceneVersions(sessionId).then(
      (found) =>
        setWords(
          new Map(
            found.flatMap(({ scene, versions }) => {
              const inAd = (versions ?? []).find((one) => one.in_ad);
              return inAd ? [[scene, inAd] as const] : [];
            }),
          ),
        ),
      () => undefined,
    );
  }, [latest, sessionId, attachment.url]);

  function choose(scene: number) {
    setSelected(scene);
    const span = spans.find((one) => one.scene === scene);
    const player = video.current;
    if (span && player) {
      player.currentTime = span.start + 0.01;
      stopAt.current = span.end;
      void player.play().catch(() => undefined);
    }
  }

  const noted = [...notes].filter(([, note]) => note.trim() !== "").map(([scene]) => scene);
  const wordless = [...photos].find(
    ([scene, files]) => files.length > 0 && !(notes.get(scene) ?? "").trim(),
  );

  async function fix() {
    if (noted.length === 0 || wordless) return;
    setSending(true);
    setError(null);
    try {
      const files = noted.flatMap((scene) => photos.get(scene) ?? []);
      const fileScenes = noted.flatMap((scene) => (photos.get(scene) ?? []).map(() => scene));
      await onSend(notesMessage(notes), files, fileScenes);
      setNotes(new Map());
      setPhotos(new Map());
      setSelected(null);
    } catch (problem) {
      setError(problem instanceof Error ? problem.message : "Couldn't send your notes.");
    } finally {
      setSending(false);
    }
  }

  const playing = spans.find((span) => now >= span.start && now < span.end)?.scene ?? null;
  const name = (scene: number) => `Scene ${scene}`;
  const time = (seconds: number) =>
    `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, "0")}`;

  return (
    <div className={`finished-ad${latest ? " with-scenes" : ""}`} role="group" aria-label="Your ad">
      <div className="finished-ad-top">
        <strong>Your ad</strong>
        <a className="finished-ad-download" href={attachment.url} download>
          Download
        </a>
      </div>
      <div className="finished-ad-body">
        <div className="finished-ad-player">
          <video
            ref={video}
            className="video"
            controls
            playsInline
            src={attachment.url}
            aria-label="Video the agent made"
            onLoadedMetadata={(event) => setLength(event.currentTarget.duration)}
            onTimeUpdate={(event) => {
              const at = event.currentTarget.currentTime;
              setNow(at);
              if (stopAt.current !== null && at >= stopAt.current - 0.05) {
                event.currentTarget.pause();
                stopAt.current = null;
              }
            }}
            onSeeking={(event) => {
              // A seek the user makes on the player itself plays on freely.
              if (event.currentTarget.paused) stopAt.current = null;
            }}
          />
          {latest && spans.length > 0 && length > 0 && (
            <div className="ad-bar" role="group" aria-label="The ad's scenes on its time bar">
              {spans.map((span) => {
                const part = Math.max(0, Math.min(1, (now - span.start) / (span.end - span.start)));
                return (
                  <button
                    key={span.scene}
                    type="button"
                    className={`ad-seg${selected === span.scene ? " on" : ""}${noted.includes(span.scene) ? " noted" : ""}`}
                    style={{ flex: span.end - span.start }}
                    aria-label={`${name(span.scene)}, ${time(span.start)} to ${time(span.end)}`}
                    onClick={() => choose(span.scene)}
                  >
                    <i className="ad-seg-fill" style={{ width: `${part * 100}%` }} />
                    <span>{span.scene}</span>
                  </button>
                );
              })}
            </div>
          )}
        </div>
        {latest && (
          <div className="ad-scenes">
            <div className="ad-scenes-head">
              <strong>Scenes</strong>
              <span>Something off? Tap the scene and write what to change.</span>
            </div>
            <ol className="ad-scene-list">
              {scenes.map((scene) => {
                const card = cards.find((one) => one.scene === scene);
                const span = spans.find((one) => one.scene === scene);
                const said = words.get(scene);
                const on = selected === scene;
                const files = photos.get(scene) ?? [];
                const busy = card?.busy ?? false;
                return (
                  <li
                    key={scene}
                    className={`ad-scene${on ? " on" : ""}${playing === scene ? " playing" : ""}`}
                  >
                    <button type="button" className="ad-scene-row" onClick={() => choose(scene)}>
                      {card?.picture ? <img src={card.picture.url} alt="" /> : <i />}
                      <span className="ad-scene-words">
                        <b>
                          {name(scene)}
                          {span && (
                            <small>
                              {time(span.start)}–{time(span.end)}
                            </small>
                          )}
                        </b>
                        <span className="ad-scene-says">
                          {said?.shows || (said?.line ? `“${said.line}”` : "")}
                        </span>
                      </span>
                      {busy ? (
                        <span className="ad-chip busy">Being fixed</span>
                      ) : noted.includes(scene) ? (
                        <span className="ad-chip note">Note</span>
                      ) : null}
                    </button>
                    {on && (
                      <div className="ad-note">
                        <div className="ad-note-field">
                          <textarea
                            autoFocus
                            rows={3}
                            aria-label={`Note for ${name(scene).toLowerCase()}`}
                            placeholder={`What should change in scene ${scene}?`}
                            value={notes.get(scene) ?? ""}
                            onChange={(event) =>
                              setNotes((had) => new Map(had).set(scene, event.target.value))
                            }
                          />
                          <label className="icon-button" title="Add photos">
                            <ImageIcon />
                            <input
                              className="visually-hidden"
                              type="file"
                              accept="image/*"
                              multiple
                              aria-label={`Photos for ${name(scene).toLowerCase()}`}
                              onChange={(event) => {
                                const picked = Array.from(event.target.files ?? []);
                                setPhotos((had) =>
                                  new Map(had).set(scene, [...files, ...picked].slice(0, 3)),
                                );
                                event.target.value = "";
                              }}
                            />
                          </label>
                        </div>
                        {files.length > 0 && (
                          <p className="ad-note-hint">
                            {files.length} photo{files.length === 1 ? "" : "s"} added.{" "}
                            <button
                              type="button"
                              className="link-button"
                              onClick={() => setPhotos((had) => new Map(had).set(scene, []))}
                            >
                              Remove
                            </button>
                          </p>
                        )}
                        <p className="ad-note-hint">
                          You can add up to 3 photos, for example how the product really looks.
                        </p>
                      </div>
                    )}
                  </li>
                );
              })}
            </ol>
          </div>
        )}
      </div>
      {latest && (
        <div className="ad-fixbar">
          {error ? (
            <p role="alert">{error}</p>
          ) : wordless ? (
            <p>Say in scene {wordless[0]}'s note what to take from its photos.</p>
          ) : (
            <p>
              Write notes on as many scenes as you like, then fix them together. The other scenes
              stay as they are.
            </p>
          )}
          <button
            type="button"
            className="ad-fix"
            disabled={sending || noted.length === 0 || wordless !== undefined}
            onClick={fix}
          >
            {sending
              ? "Sending…"
              : noted.length === 0
                ? "Fix scenes"
                : `Fix ${noted.length} scene${noted.length === 1 ? "" : "s"}`}
          </button>
        </div>
      )}
    </div>
  );
}
