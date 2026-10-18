import type { Attachment, Message, PlanNotice, SceneStep } from "./api";

/** One version of a scene's clip, with the starting picture it was made from (the scene's
 * latest picture when the clip landed), which is its first frame. */
export type SceneClip = { file: Attachment; picture: Attachment | null };

/** What the chat shows of one scene: every clip made for it, oldest first, its latest
 * starting picture, what is being made for it now, and what its last try failed to make. */
export type SceneCard = {
  scene: number;
  clips: SceneClip[];
  picture: Attachment | null;
  /** A starting picture landed after the latest clip, so that clip will be made again from
   * it (a new picture always means a new clip). Until it is, the card keeps the old clip. */
  clipOutOfDate: boolean;
  /** What is being made for the scene now, and whether it replaces one already made. */
  making: { kind: SceneStep["kind"]; remaking: boolean } | null;
  /** What the scene's last try ended with nothing made for, until something starts again. */
  failed: SceneStep["kind"] | null;
  /** The scene has a clip and a new one is on its way: its picture or clip is being made
   * again, or a new picture means a new clip while the ad is still being worked on, and
   * nothing has failed. Until the new clip lands, the card keeps the old one. Which step is
   * redone isn't the owner's concern. */
  beingRemade: boolean;
  /** Something is being made for the scene now: first time or again. */
  busy: boolean;
};

/** One card per scene that has a starting picture or a clip, or one being made, in scene
 * order. Built from the whole conversation in order, so the newest message about a scene
 * wins, and reopening a session gives the same cards as having watched it. Files without a
 * scene number (the finished ad, the portrait, the voice, the owner's photos) make no card. */
export function sceneCards(messages: Message[]): SceneCard[] {
  const cards = new Map<number, SceneCard>();
  const card = (scene: number) => {
    let found = cards.get(scene);
    if (found === undefined) {
      found = {
        scene,
        clips: [],
        picture: null,
        clipOutOfDate: false,
        making: null,
        failed: null,
        beingRemade: false,
        busy: false,
      };
      cards.set(scene, found);
    }
    return found;
  };

  for (const message of messages) {
    for (const attachment of message.attachments) {
      if (attachment.scene === null) continue;
      const scene = card(attachment.scene);
      if (attachment.kind === "picture") {
        scene.picture = attachment;
        scene.clipOutOfDate = scene.clips.length > 0;
      } else {
        scene.clips.push({ file: attachment, picture: scene.picture });
        scene.clipOutOfDate = false;
      }
    }

    const step = message.step;
    if (step === null) continue;
    const scene = card(step.scene);
    if (step.state === "started") {
      const made = step.kind === "clip" ? scene.clips.length > 0 : scene.picture !== null;
      scene.making = { kind: step.kind, remaking: made };
      scene.failed = null;
      continue;
    }
    if (scene.making?.kind === step.kind) scene.making = null;
    scene.failed = step.state === "not_made" ? step.kind : null;
  }

  // A new picture only promises a new clip while something is still being made: a run that
  // stopped in between leaves the scene waiting, not busy.
  // A scene whose new picture failed its fix check waits for the owner's answer, not the run.
  const working = [...cards.values()].some((scene) => scene.making !== null);
  const waiting = suggestedNotes(messages);
  for (const scene of cards.values()) {
    scene.beingRemade =
      scene.clips.length > 0 &&
      scene.failed === null &&
      (scene.making !== null || (scene.clipOutOfDate && working && !waiting.has(scene.scene)));
    scene.busy = scene.making !== null || scene.beingRemade;
  }
  return [...cards.values()].sort((a, b) => a.scene - b.scene);
}

/** The owner's notes as the one message that carries them: a "Scene N: ..." entry per scene
 * with a note, in scene order, each note kept whole (its line breaks too). Blank notes are
 * left out; no notes at all is an empty message. */
export function notesMessage(notes: ReadonlyMap<number, string>): string {
  return nonBlankNotes(notes)
    .sort(([a], [b]) => a - b)
    .map(([scene, note]) => `${notePrefix(scene)}${note.trim()}`)
    .join("\n");
}

/** The notes that say something: blank ones are dropped. */
export function nonBlankNotes(notes: ReadonlyMap<number, string>): [number, string][] {
  return [...notes].filter(([, note]) => note.trim() !== "");
}

/** How a note on a scene starts in the notes message. A message the owner types the same
 * way counts as a note too: it is one. */
function notePrefix(scene: number | string) {
  return `Scene ${scene}: `;
}

const NOTES = new RegExp(`^${notePrefix("\\d+")}`);

/** The scenes a notes message is about: one per line that starts a note. */
function notedScenes(text: string): number[] {
  const starts = text.matchAll(new RegExp(`^${notePrefix("(\\d+)")}`, "gm"));
  return [...starts].map(([, scene]) => Number(scene));
}

/** The scenes a message names anywhere, as "scene 4" or "Scene 4". */
function namedScenes(text: string): number[] {
  return [...text.matchAll(/\bscene (\d+)\b/gi)].map(([, scene]) => Number(scene));
}

/** The plan whose yes button is on offer, and the message showing it, or null: the newest
 * message about any plan shows one that was still waiting when read and has something to
 * do. A tap of its yes, code saying it is going ahead or was dropped, or a newer plan, all
 * come after it, so any of them takes the yes away. A typed answer is about no plan and
 * leaves it on offer. */
export function planOnOffer(messages: Message[]): { seq: number; plan: PlanNotice } | null {
  const newest = [...messages].reverse().find((one) => one.plan !== null);
  const shown = newest?.plan?.event === "shown" ? newest.plan.notice : null;
  if (newest === undefined || shown === null || shown.state !== "proposed") return null;
  const doable = shown.scenes.some((scene) => scene.action !== "no_change" && scene.cant === "");
  return doable ? { seq: newest.seq, plan: shown } : null;
}

/** Whether the producer is waiting on the owner's yes: the owner sent notes, the producer's
 * latest words answer them (its restatement, or a new one after a correction), and no redo
 * of a scene the fix is about has started since, which would mean the yes was already given:
 * a noted scene, or one the owner or the producer named since, as when a correction moves
 * the fix. Another scene's step starting, as while it is still being made for the first
 * time, says nothing about the yes. */
export function waitsForYes(messages: Message[]): boolean {
  // Once code posts plans, the yes is the plan's own (planOnOffer).
  if (messages.some((one) => one.plan !== null)) return false;
  const notesAgo = [...messages]
    .reverse()
    .findIndex((one) => one.role === "user" && NOTES.test(one.text));
  if (notesAgo === -1) return false;
  const since = messages.slice(messages.length - notesAgo);
  const noted = new Set([
    ...notedScenes(messages[messages.length - 1 - notesAgo].text),
    ...since.filter((one) => one.role !== "notice").flatMap((one) => namedScenes(one.text)),
  ]);
  if (since.some(({ step }) => step?.state === "started" && noted.has(step.scene))) return false;
  const lastSaid = since
    .reverse()
    .find((one) => one.role !== "notice" && one.step?.state !== "made");
  return lastSaid?.role === "agent";
}

/** A note suggested for a scene's card, and the message that suggested it. */
export type SuggestedNote = { text: string; seq: number };

/** The note each scene's card is filled with until the owner writes their own: the newest
 * one a message suggests for it, as when the check of a fix found the change missing. Once
 * the owner has sent anything since, it was answered and fills nothing. */
export function suggestedNotes(messages: Message[]): Map<number, SuggestedNote> {
  const suggested = new Map<number, SuggestedNote>();
  for (const message of messages) {
    if (message.role === "user") suggested.clear();
    if (message.suggested_note)
      suggested.set(message.suggested_note.scene, {
        text: message.suggested_note.text,
        seq: message.seq,
      });
  }
  return suggested;
}

/** What the owner typed in a scene's box, and the suggestion it was there when they did, if
 * any: a newer suggestion replaces it. */
export type TypedNote = { text: string; over: number | null };

/** Each scene's note: what the owner typed, unless a suggestion came after it, then the
 * suggestion. */
export function sceneNotes(
  suggested: ReadonlyMap<number, SuggestedNote>,
  typed: ReadonlyMap<number, TypedNote>,
): Map<number, string> {
  const notes = new Map<number, string>();
  for (const [scene, note] of typed) notes.set(scene, note.text);
  for (const scene of suggestionShown(suggested, typed)) {
    notes.set(scene, suggested.get(scene)!.text);
  }
  return notes;
}

/** The scenes whose box still holds the suggestion as it came: the owner hasn't typed in it
 * since. */
export function suggestionShown(
  suggested: ReadonlyMap<number, SuggestedNote>,
  typed: ReadonlyMap<number, TypedNote>,
): Set<number> {
  return new Set(
    [...suggested]
      .filter(([scene, suggestion]) => typed.get(scene)?.over !== suggestion.seq)
      .map(([scene]) => scene),
  );
}

/** Whether the finished ad has been made: a video that is no scene's clip. */
export function adFinished(messages: Message[]): boolean {
  return messages.some((message) =>
    message.attachments.some((one) => one.kind === "video" && one.scene === null),
  );
}
