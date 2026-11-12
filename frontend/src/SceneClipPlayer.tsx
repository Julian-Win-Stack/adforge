import { useEffect, useMemo, useRef, useState } from "react";
import type { Caption } from "./api";
import { FullscreenIcon } from "./icons";

/** The browser owns caption timing, seeking and native fullscreen playback (including iOS). */
export function SceneClipPlayer({
  url,
  poster,
  captions,
  scene,
  label,
  autoPlay,
}: {
  url: string;
  poster: string | null;
  captions: Caption[];
  scene: number;
  label: string;
  autoPlay: boolean;
}) {
  const player = useRef<HTMLDivElement>(null);
  const video = useRef<HTMLVideoElement>(null);
  const [fullscreen, setFullscreen] = useState(false);
  const [error, setError] = useState(false);
  const track = useMemo(() => captionTrack(captions), [captions]);

  useEffect(() => {
    const changed = () => setFullscreen(document.fullscreenElement === player.current);
    document.addEventListener("fullscreenchange", changed);
    return () => document.removeEventListener("fullscreenchange", changed);
  }, []);

  async function toggleFullscreen() {
    setError(false);
    try {
      if (document.fullscreenElement === player.current) {
        await document.exitFullscreen();
      } else if (player.current?.requestFullscreen) {
        await player.current.requestFullscreen();
      } else {
        // iPhone Safari opens the video itself; its native player keeps the caption track.
        const native = video.current as
          (HTMLVideoElement & { webkitEnterFullscreen?: () => void }) | null;
        if (!native?.webkitEnterFullscreen) throw new Error("Fullscreen unavailable");
        native.webkitEnterFullscreen();
      }
    } catch {
      setError(true);
    }
  }

  return (
    <div className="scene-clip-player" ref={player}>
      <video
        ref={video}
        src={poster ? url : `${url}#t=0.1`}
        poster={poster ?? undefined}
        controls
        controlsList="nofullscreen"
        playsInline
        autoPlay={autoPlay}
        preload="metadata"
        aria-label={label}
      >
        {captions.length > 0 && (
          <track
            key={track}
            kind="captions"
            label="Captions"
            src={track}
            default
            onLoad={(event) => {
              event.currentTarget.track.mode = "showing";
            }}
          />
        )}
      </video>
      <button
        type="button"
        className="scene-fullscreen"
        onClick={toggleFullscreen}
        aria-label={fullscreen ? "Exit fullscreen" : `View scene ${scene} fullscreen`}
        title={fullscreen ? "Exit fullscreen" : "Fullscreen"}
      >
        <FullscreenIcon />
      </button>
      {error && (
        <p className="scene-fullscreen-error" role="alert">
          Couldn't open fullscreen. Try again or use another browser.
        </p>
      )}
    </div>
  );
}

function timestamp(seconds: number): string {
  const ms = Math.round(Math.max(0, seconds) * 1000);
  const hours = Math.floor(ms / 3_600_000);
  const minutes = Math.floor((ms % 3_600_000) / 60_000);
  const whole = Math.floor((ms % 60_000) / 1000);
  return `${[hours, minutes, whole].map((part) => String(part).padStart(2, "0")).join(":")}.${String(ms % 1000).padStart(3, "0")}`;
}

function captionTrack(captions: Caption[]): string {
  const cues = captions.map(({ start, end, text }) => {
    // Caption words are plain text, never WebVTT markup.
    const escaped = text.replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;");
    return `${timestamp(start)} --> ${timestamp(end)} line:75% position:50% align:center size:90%\n${escaped}\n`;
  });
  return `data:text/vtt;charset=utf-8,${encodeURIComponent(`WEBVTT\n\n${cues.join("\n")}`)}`;
}
