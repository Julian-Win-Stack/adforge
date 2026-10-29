import type { Attachment, CardVersion, Message, SceneStep, SentList } from "./api";

/** One version of a scene's clip, with the starting picture it was made from (the scene's
 * latest picture when the clip landed), which is its first frame, and what was sent to
 * make it, when anything is known. */
export type SceneClip = { file: Attachment; picture: Attachment | null; sent: ClipSent | null };

/** What a scene card shows of what one clip was sent: the pictures in the order they went
 * (Image 1 first), the video pieces with the seconds sent, the labels of the pictures left
 * out, and each picture whose recolour fell back so it went in its own colour. */
export type ClipSent = {
  pictures: { image: number; label: string; url: string }[];
  pieces: { number: number; url: string; start: number; end: number }[];
  leftOut: string[];
  notRecoloured: { label: string; colour: string }[];
};

/** What a clip's attachment says it was sent, or null when it says nothing: a clip made
 * before anything was recorded carries the empty list. */
export function clipSent(clip: Attachment): ClipSent | null {
  return sentShown(clip.sent);
}

/** What a sent list says a clip was sent, as a card shows it, or null when it says nothing.
 * The one place the chat's sent list is read. */
export function sentShown(sent: SentList | null | undefined): ClipSent | null {
  if (!sent || (sent.pictures.length === 0 && sent.pieces.length === 0)) return null;
  return {
    pictures: sent.pictures.map(({ image, label, url }) => ({ image, label, url })),
    pieces: sent.pieces.map(({ number, url, start, end }) => ({ number, url, start, end })),
    leftOut: sent.left_out.map(({ label }) => label),
    notRecoloured: sent.recolours
      .filter(({ outcome }) => outcome === "fell_back")
      .map(({ of, colour }) => ({
        label:
          [...sent.pictures, ...sent.left_out].find((one) => one.library_number === of)?.label ??
          "",
        colour,
      })),
  };
}

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
  /** The fixing agent's question for the shop owner about the scene, in its words, while it
   * waits for an answer: until it is answered or something starts being made for the scene
   * again. */
  question: string | null;
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
        question: null,
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
        scene.clips.push({ file: attachment, picture: scene.picture, sent: clipSent(attachment) });
        scene.clipOutOfDate = false;
      }
    }

    // A give-up's try kept in the ad is never posted as a file of its own: the notice of
    // every try carries it, and what it was sent.
    const kept = message.tries?.clips.find((one) => one.clip === message.tries?.in_ad);
    if (message.tries && kept) {
      const scene = card(message.tries.scene);
      if (scene.clips.at(-1)?.file.url !== kept.url) {
        const file: Attachment = {
          position: 0,
          kind: "video",
          url: kept.url,
          scene: message.tries.scene,
          version: kept.version,
          sent: kept.sent ?? null,
        };
        scene.clips.push({ file, picture: scene.picture, sent: clipSent(file) });
        scene.clipOutOfDate = false;
      }
    }

    if (message.question) {
      card(message.question.scene).question = message.question.open ? message.question.text : null;
    }

    const step = message.step;
    if (step === null) continue;
    const scene = card(step.scene);
    if (step.state === "started") {
      scene.question = null;
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

/** How a note on a scene starts in the notes message. */
function notePrefix(scene: number | string) {
  return `Scene ${scene}: `;
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

/** Whether the finished ad has been made: the producer's message showing it carries that one
 * video and no scene's. The fix check's notice shows a scene's old and new clips without
 * their scene, so it is no finished ad. */
export function adFinished(messages: Message[]): boolean {
  return messages.some(
    ({ role, attachments }) =>
      role === "agent" &&
      attachments.length === 1 &&
      attachments[0].kind === "video" &&
      attachments[0].scene === null,
  );
}

/** What a scene's card plays and names: the version being previewed when there is one, else
 * the one in the ad. Null when neither is known, as when no clip says the scene's line as it
 * stands: the card then shows its newest clip from the chat. */
export function versionOnShow(
  versions: CardVersion[],
  previewing: number | null,
): CardVersion | null {
  return (
    versions.find((one) => one.number === previewing) ?? versions.find((one) => one.in_ad) ?? null
  );
}

/** Whether the clip judge passed none of a scene's clips and failed one: the card asks the
 * owner to pick the best one. A scene it never failed, as a talking one it never looks at,
 * isn't asked. */
export function noVersionPassed(versions: CardVersion[]): boolean {
  return (
    !versions.some((one) => one.verdict === "pass") &&
    versions.some((one) => one.verdict === "fail")
  );
}
