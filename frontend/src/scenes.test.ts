// @vitest-environment node
import { expect, test } from "vitest";
import type { Attachment, Message, SceneStep } from "./api";
import {
  notesMessage,
  sceneCards,
  sceneNotes,
  suggestedNotes,
  tryInAd,
  waitsForYes,
} from "./scenes";

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
    suggested_note: null,
    plan: null,
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

test("a picture and its clip landing together leave the card on the clip, made from that picture", () => {
  const [card] = sceneCards([message([picture(3, "/p3.png")]), message([clip(3, "/c3.mp4")])]);
  expect(card).toMatchObject({
    scene: 3,
    clipOutOfDate: false,
    clips: [{ file: { url: "/c3.mp4" }, picture: { url: "/p3.png" } }],
  });
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
    "another scene's picture started since the restatement",
    [
      said("user", "Scene 2: Scrub"),
      said("agent", "Shall I?"),
      said("notice", "Making scene 3's picture.", {
        scene: 3,
        kind: "starting_picture",
        state: "started",
      }),
    ],
    true,
  ],
  [
    "a redo of the second noted scene started since",
    [
      said("user", "Scene 2: Scrub\nthe tile\nScene 4: Brighter"),
      said("agent", "Shall I?"),
      said("user", "Yes"),
      said("notice", "Remaking scene 4's picture.", {
        scene: 4,
        kind: "starting_picture",
        state: "started",
      }),
      said("agent", "On it."),
    ],
    false,
  ],
  [
    "a correction moves the fix to another scene, said yes to and started",
    [
      said("user", "Scene 2: Scrub"),
      said("agent", "Shall I?"),
      said("user", "Actually leave scene 2, make scene 4 brighter instead"),
      said("agent", "Scene 4 brighter, shall I?"),
      said("user", "Yes"),
      said("notice", "Remaking scene 4's picture.", {
        scene: 4,
        kind: "starting_picture",
        state: "started",
      }),
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
  [
    "a note typed in the chat box",
    [said("user", "Scene 1: make it red"), said("agent", "Red?")],
    true,
  ],
  ["a note on a two-digit scene", [said("user", "Scene 12: slower"), said("agent", "Ok?")], true],
  ["the word Scene mid-message", [said("user", "About Scene 2: hmm"), said("agent", "Ok")], false],
  ["notes from the agent", [said("agent", "Scene 2: planned"), said("agent", "Ok")], false],
] as const)("waits for a yes: %s", (_, messages, expected) => {
  expect(waitsForYes([...messages])).toBe(expected);
});

function suggesting(scene: number, text: string): Message {
  return { ...message([]), role: "notice", suggested_note: { scene, text } };
}

function owner(text: string): Message {
  return { ...message([]), role: "user", text };
}

function texts(suggested: ReturnType<typeof suggestedNotes>) {
  return new Map([...suggested].map(([scene, note]) => [scene, note.text]));
}

test("each scene's newest suggested note fills its box until the owner sends anything", () => {
  expect(suggestedNotes([])).toEqual(new Map());
  expect(
    texts(suggestedNotes([suggesting(2, "a"), suggesting(3, "b"), suggesting(2, "c")])),
  ).toEqual(
    new Map([
      [2, "c"],
      [3, "b"],
    ]),
  );
  expect(suggestedNotes([suggesting(2, "a"), owner("go ahead")])).toEqual(new Map());
  expect(
    texts(suggestedNotes([suggesting(2, "a"), owner("go ahead"), suggesting(3, "b")])),
  ).toEqual(new Map([[3, "b"]]));
});

test("what the owner typed wins over the suggestion it was typed over, not a newer one", () => {
  const suggested = new Map([[2, { text: "still hovers", seq: 9 }]]);
  expect(sceneNotes(suggested, new Map())).toEqual(new Map([[2, "still hovers"]]));
  expect(sceneNotes(suggested, new Map([[2, { text: "", over: 9 }]]))).toEqual(new Map([[2, ""]]));
  expect(sceneNotes(suggested, new Map([[2, { text: "", over: 4 }]]))).toEqual(
    new Map([[2, "still hovers"]]),
  );
  expect(sceneNotes(suggested, new Map([[3, { text: "slower", over: null }]]))).toEqual(
    new Map([
      [3, "slower"],
      [2, "still hovers"],
    ]),
  );
});

test("a scene whose new picture failed its fix check waits for the owner, not being remade", () => {
  const before = [
    message([picture(1, "/p1-a.png")]),
    message([clip(1, "/c1-a.mp4")]),
    message([picture(2, "/p2-a.png")]),
    message([clip(2, "/c2-a.mp4")]),
    // Scene 2's new picture lands; scene 1's fix is still being made.
    message([], { scene: 1, kind: "clip", state: "started" }),
    message([picture(2, "/p2-b.png")]),
  ];
  expect(sceneCards(before)[1].beingRemade).toBe(true);

  // Its fix check failed: the owner is asked, so it isn't on its way.
  const [, waiting] = sceneCards([...before, suggesting(2, "The handle still faces away.")]);
  expect(waiting).toMatchObject({ beingRemade: false, busy: false, clipOutOfDate: true });

  const [, answered] = sceneCards([
    ...before,
    suggesting(2, "The handle still faces away."),
    owner("Go ahead"),
  ]);
  expect(answered.beingRemade).toBe(true);
});

function triesNotice(inAd: number | null): Message {
  return {
    ...message([]),
    role: "notice",
    level: "info",
    tries: {
      scene: 2,
      in_ad: inAd,
      clips: [
        { clip: 11, name: "First try", version: "v1", url: "/t1.mp4", problem: "Floats." },
        { clip: 12, name: "Remake 1", version: "v2", url: "/t2.mp4", problem: "Spins." },
        { clip: 13, name: "Remake 2", version: "v3", url: "/t3.mp4", problem: "Spins." },
      ],
    },
  };
}

function tap(clip: number): Message {
  return { ...message([]), role: "user", text: "Use it", use_clip: clip };
}

test("the try in the ad is the one the notice was read with until something changes it", () => {
  const notice = triesNotice(11);
  expect(tryInAd([message([clip(1, "/c1.mp4")]), notice], notice.seq)).toBe(11);
});

test("a tap puts the try chosen in the ad at once", () => {
  const notice = triesNotice(11);
  expect(tryInAd([notice, tap(12)], notice.seq)).toBe(12);
});

test("the scene's clip shown again as a try, as when gone back to, is that try", () => {
  const notice = triesNotice(11);
  expect(tryInAd([notice, message([clip(2, "/t3.mp4")])], notice.seq)).toBe(13);
});

test("a newer clip of the scene takes every try out of the ad; another scene's doesn't", () => {
  const notice = triesNotice(11);
  expect(tryInAd([notice, message([clip(3, "/c3.mp4")])], notice.seq)).toBe(11);
  expect(tryInAd([notice, message([clip(2, "/new.mp4")])], notice.seq)).toBeNull();
});
