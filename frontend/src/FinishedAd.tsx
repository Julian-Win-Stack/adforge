import { useRef, useState } from "react";
import type { Attachment } from "./api";
import { SceneCardView } from "./ScenePanel";
import { fixLabel, wordlessHint, type SceneWork, type WatchClip } from "./sceneWork";

/** The newest finished ad, with its scenes beside it (spec #729): a row per scene in the
 * order they play, and a time bar under the player split into the scenes. A tap on a row, or
 * on its part of the bar, plays the ad from that scene on and opens the row, which holds all a
 * scene card offers: its note, and its versions to preview and swap in. "Fix N scenes" sends
 * every note at once. An ad made before scene times were kept lists its scenes without times,
 * and a tap only opens the row. */
export function FinishedAd({
  attachment,
  work,
  watch,
}: {
  attachment: Attachment;
  work: SceneWork;
  /** A chat notice asked for a scene's clip: its row opens to show it. */
  watch: WatchClip | null;
}) {
  const video = useRef<HTMLVideoElement>(null);
  const [now, setNow] = useState(0);
  const [playing, setPlaying] = useState(false);
  // The row the owner opened, unless a chat notice asked for a scene since.
  const chosen = work.opened;
  const opened = watch && chosen?.request !== watch.request ? watch.scene : (chosen?.scene ?? null);

  const times = attachment.scene_times ?? [];
  const order = times.length > 0 ? times.map(({ scene }) => scene) : work.cards.map((c) => c.scene);
  const rows = order.flatMap((scene) => {
    const card = work.cards.find((one) => one.scene === scene);
    return card ? [{ card, time: times.find((one) => one.scene === scene) ?? null }] : [];
  });
  const noted = new Set(work.noted);
  const hint = wordlessHint(work.wordless);

  function choose(scene: number) {
    work.open(scene, watch?.request);
    const time = times.find((one) => one.scene === scene);
    const player = video.current;
    if (!time || !player) return;
    player.currentTime = time.start;
    // A browser that won't play without a tap first still shows the scene's first frame.
    void player.play()?.catch(() => undefined);
  }

  return (
    <div className="finished-ad with-scenes" role="group" aria-label="Your ad">
      <FinishedAdTop url={attachment.url} />
      <div className="finished-ad-body">
        <div className="finished-ad-player">
          <video
            ref={video}
            className="video"
            controls
            playsInline
            src={attachment.url}
            aria-label="Video the agent made"
            onPlay={() => setPlaying(true)}
            onPause={() => setPlaying(false)}
            onEnded={() => setPlaying(false)}
            onTimeUpdate={(event) => setNow(event.currentTarget.currentTime)}
          />
          {times.length > 0 && (
            <div className="ad-bar" role="group" aria-label="The ad's scenes">
              {times.map((time) => {
                const length = time.end - time.start;
                const part = length > 0 ? (now - time.start) / length : 0;
                return (
                  <button
                    key={time.scene}
                    type="button"
                    className={`ad-bar-part${opened === time.scene ? " on" : ""}${noted.has(time.scene) ? " noted" : ""}`}
                    style={{ flexGrow: length }}
                    aria-label={`Scene ${time.scene}, ${clock(time.start)} to ${clock(time.end)}`}
                    title={`Scene ${time.scene}`}
                    onClick={() => choose(time.scene)}
                  >
                    <i
                      className="ad-bar-fill"
                      style={{ width: `${Math.max(0, Math.min(1, part)) * 100}%` }}
                    />
                    <span>{time.scene}</span>
                  </button>
                );
              })}
            </div>
          )}
        </div>
        <div className="ad-scenes">
          <div className="ad-scenes-head">
            <strong>Scenes</strong>
            <span>Something off? Tap the scene and write what to change.</span>
          </div>
          <ol className="ad-scene-list" aria-label="Scenes">
            {rows.map(({ card, time }) => {
              const scene = card.scene;
              const inAd = work.versions.get(scene)?.find((one) => one.in_ad);
              const says = inAd?.line ? `“${inAd.line}”` : (inAd?.shows ?? "");
              const picture = inAd?.picture ?? card.picture?.url ?? null;
              const open = opened === scene;
              const playsNow = playing && time !== null && now >= time.start && now < time.end;
              return (
                <li
                  key={scene}
                  className={`ad-scene${open ? " open" : ""}${playsNow ? " playing" : ""}`}
                >
                  <button
                    type="button"
                    className="ad-scene-row"
                    aria-expanded={open}
                    onClick={() => choose(scene)}
                  >
                    <span className="ad-scene-name">
                      Scene {scene}{" "}
                      {time && (
                        <small>
                          {clock(time.start)}–{clock(time.end)}
                        </small>
                      )}
                    </span>{" "}
                    {picture ? <img src={picture} alt="" loading="lazy" /> : <i />}
                    <span className="ad-scene-says">{says}</span>{" "}
                    {card.busy ? (
                      <span className="ad-scene-mark busy">Being fixed</span>
                    ) : noted.has(scene) ? (
                      <span className="ad-scene-mark">Note</span>
                    ) : null}
                  </button>
                  {open && (
                    <SceneCardView
                      card={card}
                      watch={watch?.scene === scene ? watch : null}
                      work={work}
                      layout="row"
                    />
                  )}
                </li>
              );
            })}
          </ol>
        </div>
      </div>
      <div className="ad-fix">
        {work.error && (
          <p role="alert" className="notice info inline">
            {work.error}
          </p>
        )}
        <p className="ad-fix-hint">
          {hint ??
            "Write notes on as many scenes as you like, then fix them together. The other scenes stay as they are."}
        </p>
        <button
          type="button"
          className="scene-send-button"
          disabled={work.sending || !work.sendable}
          onClick={() => void work.send()}
        >
          {fixLabel(work.noted.length)}
        </button>
      </div>
    </div>
  );
}

/** The top of every finished ad's card: its name, and a way to keep it. */
export function FinishedAdTop({ url }: { url: string }) {
  return (
    <div className="finished-ad-top">
      <strong>Your ad</strong>
      <a className="finished-ad-download" href={url} download>
        Download
      </a>
    </div>
  );
}

/** Seconds as the player says them: 0:08. */
function clock(seconds: number) {
  const whole = Math.floor(seconds);
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, "0")}`;
}
