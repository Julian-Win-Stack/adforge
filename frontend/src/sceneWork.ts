import { useEffect, useMemo, useRef, useState } from "react";
import { ApiError, getSceneVersions, putVersionInAd } from "./api";
import type { CardVersion, Message } from "./api";
import {
  nonBlankNotes,
  notesMessage,
  sceneCards,
  sceneNotes,
  suggestedNotes,
  suggestionShown,
  type SceneCard,
} from "./scenes";

/** The photos the owner may send with one scene's note (Julian, Oct 15). */
export const MOST_NOTE_PHOTOS = 3;

/** A photo or video sent with a scene's note, and that scene. */
export type NoteAttachment = { file: File; scene: number };

/** A chat notice asks for this exact clip to be opened; each tap is a new request. */
export type WatchClip = { scene: number; clip: number; request: number };

/** Everything the owner can do to the ad's scenes, wherever they do it: the scenes panel
 * beside the chat and the scene list under the finished ad share one of these, so a note
 * written in one shows in the other and goes once. */
export type SceneWork = {
  cards: SceneCard[];
  /** Each scene's clips as the server has them now: empty until it has said. */
  versions: ReadonlyMap<number, CardVersion[]>;
  /** Put a scene's version in the ad; rejects with the server's reason when it can't. */
  putInAd: (scene: number, version: number) => Promise<void>;
  /** What each scene's note box holds: what the owner typed, else the suggestion. */
  notes: ReadonlyMap<number, string>;
  /** The scenes whose note is still the agent's suggestion, untouched by the owner. */
  suggested: ReadonlySet<number>;
  setNote: (scene: number, note: string) => void;
  attachments: ReadonlyMap<number, File[]>;
  addAttachments: (scene: number, files: Iterable<File>) => void;
  removeAttachment: (scene: number, index: number) => void;
  /** The scenes with words in their note, in scene order. */
  noted: number[];
  /** Scenes with a file but no words saying what to take from it, in scene order. */
  wordless: [number, File[]][];
  sendable: boolean;
  sending: boolean;
  error: string | null;
  /** Sends every note as one message, one "Scene N: ..." line each, every file with its
   * scene; the notes clear once it is taken. */
  send: () => Promise<void>;
};

export function useSceneWork({
  sessionId,
  messages,
  watch,
  onSend,
}: {
  sessionId: string | null;
  messages: Message[];
  watch: WatchClip | null;
  onSend: (text: string, attachments: NoteAttachment[]) => Promise<void>;
}): SceneWork {
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

  // A suggested note fills a scene's box until the owner types in it, clearing it included.
  const [typed, setTyped] = useState<ReadonlyMap<number, { text: string; over: number | null }>>(
    new Map(),
  );
  const suggestions = useMemo(() => suggestedNotes(messages), [messages]);
  const notes = sceneNotes(suggestions, typed);
  const [attachments, setAttachments] = useState<ReadonlyMap<number, File[]>>(new Map());
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const noted = nonBlankNotes(notes)
    .map(([scene]) => scene)
    .sort((a, b) => a - b);
  // A file goes only with words saying what to take from it.
  const wordless = [...attachments]
    .filter(([scene, added]) => added.length > 0 && !notes.get(scene)?.trim())
    .sort(([a], [b]) => a - b);
  const sendable = noted.length > 0 && wordless.length === 0;

  return {
    cards,
    versions,
    async putInAd(scene, version) {
      ++asked.current;
      const after = await putVersionInAd(sessionId as string, scene, version);
      setVersions((had) => new Map(had).set(after.scene, after.versions));
    },
    notes,
    suggested: suggestionShown(suggestions, typed),
    setNote(scene, note) {
      setTyped((had) =>
        new Map(had).set(scene, { text: note, over: suggestions.get(scene)?.seq ?? null }),
      );
    },
    attachments,
    addAttachments(scene, files) {
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
    },
    removeAttachment(scene, index) {
      setAttachments((had) =>
        new Map(had).set(
          scene,
          (had.get(scene) ?? []).filter((_, i) => i !== index),
        ),
      );
    },
    noted,
    wordless,
    sendable,
    sending,
    error,
    async send() {
      if (sending || !sendable) return;
      setSending(true);
      setError(null);
      try {
        await onSend(
          notesMessage(notes),
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
    },
  };
}

/** The send button's words: "Fix scenes" with no note, else how many scenes it fixes. */
export function fixLabel(noted: number): string {
  return noted === 0 ? "Fix scenes" : `Fix ${noted} scene${noted === 1 ? "" : "s"}`;
}

/** What the owner must still write before notes can go: the first scene with a file and no
 * words, or null. */
export function wordlessHint(wordless: [number, File[]][]): string | null {
  if (wordless.length === 0) return null;
  const [scene, files] = wordless[0];
  const kind = files.some((file) => file.type.startsWith("video/")) ? "attachment" : "photo";
  return `Say in scene ${scene}'s note what to take from its ${kind}${files.length === 1 ? "" : "s"}.`;
}
