import { useEffect, useState } from "react";
import { SparkIcon } from "./icons";
import type { SceneCard } from "./scenes";

/** What the agent is doing now, in the owner's words, from what the chat already knows:
 * clips being made, else pictures being made, each with how many scenes have theirs, else
 * plain working. Never a guess at a step the app can't see. */
export function workingStep(cards: SceneCard[]): string {
  if (cards.some((card) => card.making?.kind === "clip")) {
    const done = cards.filter(
      (card) => card.clips.length > 0 && card.making === null && !card.clipOutOfDate,
    ).length;
    return `Making scene clips · ${done} of ${cards.length} done`;
  }
  if (cards.some((card) => card.making?.kind === "starting_picture")) {
    const done = cards.filter(
      (card) => card.picture !== null && card.making?.kind !== "starting_picture",
    ).length;
    return `Making scene pictures · ${done} of ${cards.length} done`;
  }
  return "The agent is working";
}

/** Whole seconds as the clock shows them: minutes, then two-digit seconds ("1:05"). */
export function clockText(seconds: number): string {
  const whole = Math.max(0, Math.floor(seconds));
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, "0")}`;
}

/** The agent's turn in the chat: its bubble with bouncing dots, what it is doing now, and
 * how long the turn has run since `since` (the owner's last message), ticking each second. */
export function Working({ cards, since }: { cards: SceneCard[]; since: string | null }) {
  const step = workingStep(cards);
  const now = useNow();
  const started = since === null ? NaN : Date.parse(since);
  return (
    <div className="working" role="group" aria-label="The agent's turn">
      <strong className="who">
        <span className="who-mark">
          <SparkIcon size={11} />
        </span>
        Agent
      </strong>
      <div className="working-bubble">
        <span className="dots" aria-hidden="true">
          <i />
          <i />
          <i />
        </span>
        <span className="working-step" aria-live="polite">
          {step}
        </span>
        {!Number.isNaN(started) && (
          <span className="working-clock" role="timer">
            {clockText((now - started) / 1000)}
          </span>
        )}
      </div>
    </div>
  );
}

/** The time now, updated every second while the caller is on screen. */
function useNow(): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const tick = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(tick);
  }, []);
  return now;
}
