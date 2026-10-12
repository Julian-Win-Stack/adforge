import { expect, test } from "vitest";
import type { Attachment, Message, SceneStep } from "./api";
import { notesMessage, sceneCards, waitsForYes } from "./scenes";

let seq = 0;

function message(attachments: Attachment[], step: SceneStep | null = null): Message {
  seq += 1;
  return {
    seq,
    role: "agent",
    level: "",
    text: "",
    created_at: "2026-10-11T10:00:00Z",
    attachments,
    step,
  };
}

function clip(scene: number | null, url: string): Attachment {
  return { position: 1, kind: "video", url, scene };
}

test("one card per scene with a clip, in scene order whatever order they came in", () => {
  const cards = sceneCards([
    message([clip(3, "/c3.mp4")]),
    message([clip(1, "/c1.mp4")]),
    message([clip(2, "/c2.mp4")]),
  ]);
  expect(cards.map((card) => [card.scene, card.clips.map((one) => one.file.url)])).toEqual([
    [1, ["/c1.mp4"]],
    [2, ["/c2.mp4"]],
    [3, ["/c3.mp4"]],
  ]);
});

function picture(scene: number | null, url: string): Attachment {
  return { position: 1, kind: "picture", url, scene };
}

test("a scene with only a starting picture gets a card with that picture and no clip", () => {
  const [card] = sceneCards([message([picture(2, "/p2.png")])]);
  expect(card).toMatchObject({ scene: 2, clips: [], picture: { url: "/p2.png" } });
});

test("a later clip and a later picture for a scene come after the earlier ones", () => {
  const [card] = sceneCards([
    message([picture(2, "/p2-a.png")]),
    message([clip(2, "/c2-a.mp4")]),
    message([picture(2, "/p2-b.png")]),
    message([clip(2, "/c2-b.mp4")]),
  ]);
  expect(card.clips.map((one) => [one.file.url, one.picture?.url])).toEqual([
    ["/c2-a.mp4", "/p2-a.png"],
    ["/c2-b.mp4", "/p2-b.png"],
  ]);
  expect(card.picture?.url).toBe("/p2-b.png");
});

test("the finished ad, the portrait, the voice and the owner's photos make no card", () => {
  const cards = sceneCards([
    message([
      picture(null, "/portrait.png"),
      { position: 2, kind: "sound", url: "/v.mp3", scene: null },
    ]),
    message([clip(null, "/ad.mp4")]),
    { ...message([picture(null, "/mine.png")]), role: "user" },
  ]);
  expect(cards).toEqual([]);
});

function notice(step: SceneStep): Message {
  return { ...message([], step), role: "notice", level: "info", text: "Making it." };
}

test("a scene's picture or clip that started is being made, and gets a card before anything lands", () => {
  const [card] = sceneCards([notice({ scene: 4, kind: "starting_picture", state: "started" })]);
  expect(card).toMatchObject({
    scene: 4,
    clips: [],
    picture: null,
    making: { kind: "starting_picture", remaking: false },
  });
});

test("a clip started for a scene that already has one is being remade, and the old clip stays", () => {
  const [card] = sceneCards([
    message([clip(1, "/c1-a.mp4")]),
    notice({ scene: 1, kind: "clip", state: "started" }),
  ]);
  expect(card.making).toEqual({ kind: "clip", remaking: true });
  expect(card.clips.map((one) => one.file.url)).toEqual(["/c1-a.mp4"]);
});

test("a starting picture started for a scene that already has one is being remade", () => {
  const [card] = sceneCards([
    message([picture(1, "/p1-a.png")]),
    notice({ scene: 1, kind: "starting_picture", state: "started" }),
  ]);
  expect(card.making).toEqual({ kind: "starting_picture", remaking: true });
});

test.each(["made", "not_made"] as const)(
  "a scene's picture or clip ending %s is no longer being made",
  (state) => {
    const [card] = sceneCards([
      notice({ scene: 1, kind: "clip", state: "started" }),
      state === "made"
        ? message([clip(1, "/c1.mp4")], { scene: 1, kind: "clip", state })
        : { ...notice({ scene: 1, kind: "clip", state }), level: "problem" },
    ]);
    expect(card.making).toBeNull();
  },
);

test("the end of a scene's other step doesn't stop what is being made", () => {
  const [card] = sceneCards([
    notice({ scene: 1, kind: "clip", state: "started" }),
    notice({ scene: 1, kind: "starting_picture", state: "not_made" }),
  ]);
  expect(card.making).toEqual({ kind: "clip", remaking: false });
});

test("a scene whose last try ended with nothing made says which, until it starts again", () => {
  const failed = sceneCards([
    message([picture(1, "/p1.png")]),
    notice({ scene: 1, kind: "clip", state: "started" }),
    notice({ scene: 1, kind: "clip", state: "not_made" }),
  ]);
  expect(failed[0].failed).toBe("clip");

  const again = sceneCards([
    notice({ scene: 1, kind: "clip", state: "not_made" }),
    notice({ scene: 1, kind: "clip", state: "started" }),
  ]);
  expect(again[0].failed).toBeNull();

  const madeAfter = sceneCards([
    notice({ scene: 1, kind: "clip", state: "not_made" }),
    message([clip(1, "/c1.mp4")], { scene: 1, kind: "clip", state: "made" }),
  ]);
  expect(madeAfter[0].failed).toBeNull();
});

test("a starting picture newer than the scene's clip leaves the clip out of date until the next clip", () => {
  const [stale] = sceneCards([
    message([picture(1, "/p1-a.png")]),
    message([clip(1, "/c1-a.mp4")]),
    message([picture(1, "/p1-b.png")]),
  ]);
  expect(stale.clipOutOfDate).toBe(true);

  const [fresh] = sceneCards([
    message([clip(1, "/c1-a.mp4")]),
    message([picture(1, "/p1-b.png")]),
    message([clip(1, "/c1-b.mp4")]),
  ]);
  expect(fresh.clipOutOfDate).toBe(false);

  const [noClip] = sceneCards([message([picture(1, "/p1-a.png")])]);
  expect(noClip.clipOutOfDate).toBe(false);
});

const madeClip: SceneStep = { scene: 2, kind: "clip", state: "made" };

function said(role: Message["role"], text: string, step: SceneStep | null = null): Message {
  return { ...message([], step), role, text, level: role === "notice" ? "info" : "" };
}

test("the notes message: one entry per scene with a note, in scene order, trimmed", () => {
  expect(notesMessage(new Map())).toBe("");
  expect(notesMessage(new Map([[2, "  "]]))).toBe("");
  expect(
    notesMessage(
      new Map([
        [10, "Last"],
        [2, " Scrub\nthe tile \n"],
        [3, ""],
      ]),
    ),
  ).toBe("Scene 2: Scrub\nthe tile\nScene 10: Last");
});

test.each([
  ["nothing said", [], false],
  ["no notes, the agent talking", [said("agent", "Here's the ad.")], false],
  ["notes, no answer yet", [said("user", "Scene 2: Scrub")], false],
  [
    "notes, then the restatement",
    [said("user", "Scene 2: Scrub"), said("agent", "Shall I?")],
    true,
  ],
  [
    "the restatement, then a notice",
    [said("user", "Scene 2: Scrub"), said("agent", "Shall I?"), said("notice", "Saved.")],
    true,
  ],
  [
    "only a scene's clip shown since the notes",
    [said("user", "Scene 2: Scrub"), { ...said("agent", ""), step: madeClip }],
    false,
  ],
  [
    "the restatement, then a correction",
    [said("user", "Scene 2: Scrub"), said("agent", "Shall I?"), said("user", "No, the tile")],
    false,
  ],
  [
    "a correction answered",
    [
      said("user", "Scene 2: Scrub"),
      said("agent", "Shall I?"),
      said("user", "No, the tile"),
      said("agent", "The tile then?"),
    ],
    true,
  ],
  [
    "a redo started since",
    [
      said("user", "Scene 2: Scrub"),
      said("agent", "Shall I?"),
      said("user", "ok"),
      said("notice", "Remaking scene 2's clip.", { scene: 2, kind: "clip", state: "started" }),
      said("agent", "On it."),
    ],
    false,
  ],
  [
    "new notes after an earlier redo",
    [
      said("user", "Scene 2: Scrub"),
      said("notice", "Remaking.", { scene: 2, kind: "clip", state: "started" }),
      said("user", "Scene 3: Brighter"),
      said("agent", "Brighter, shall I?"),
    ],
    true,
  ],
  ["the word Scene mid-message", [said("user", "About Scene 2: hmm"), said("agent", "Ok")], false],
  ["notes from the agent", [said("agent", "Scene 2: planned"), said("agent", "Ok")], false],
] as const)("waits for a yes: %s", (_, messages, expected) => {
  expect(waitsForYes([...messages])).toBe(expected);
});
