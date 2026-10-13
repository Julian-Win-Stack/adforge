import type { Attachment, Message, SceneStep } from "./api";

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
  const working = [...cards.values()].some((scene) => scene.making !== null);
  for (const scene of cards.values()) {
    scene.beingRemade =
      scene.clips.length > 0 &&
      scene.failed === null &&
      (scene.making !== null || (scene.clipOutOfDate && working));
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

/** Whether the producer is waiting on the owner's yes: the owner sent notes, the producer's
 * latest words answer them (its restatement, or a new one after a correction), and no redo
 * has started since, which would mean the yes was already given. */
export function waitsForYes(messages: Message[]): boolean {
  const notesAgo = [...messages]
    .reverse()
    .findIndex((one) => one.role === "user" && NOTES.test(one.text));
  if (notesAgo === -1) return false;
  const since = messages.slice(messages.length - notesAgo);
  if (since.some((one) => one.step?.state === "started")) return false;
  const lastSaid = since
    .reverse()
    .find((one) => one.role !== "notice" && one.step?.state !== "made");
  return lastSaid?.role === "agent";
}
