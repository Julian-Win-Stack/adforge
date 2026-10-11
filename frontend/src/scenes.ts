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
  /** What is being made for the scene now, and whether it replaces one already made. */
  making: { kind: SceneStep["kind"]; remaking: boolean } | null;
  /** What the scene's last try ended with nothing made for, until something starts again. */
  failed: SceneStep["kind"] | null;
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
      found = { scene, clips: [], picture: null, making: null, failed: null };
      cards.set(scene, found);
    }
    return found;
  };

  for (const message of messages) {
    for (const attachment of message.attachments) {
      if (attachment.scene === null) continue;
      const scene = card(attachment.scene);
      if (attachment.kind === "picture") scene.picture = attachment;
      else scene.clips.push({ file: attachment, picture: scene.picture });
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

  return [...cards.values()].sort((a, b) => a.scene - b.scene);
}
