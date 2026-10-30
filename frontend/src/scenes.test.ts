// @vitest-environment node
import { expect, test } from "vitest";
import type { Attachment, CardVersion, Message, SceneStep } from "./api";
import {
  noVersionPassed,
  notesMessage,
  sceneCards,
  sceneNotes,
  suggestedNotes,
  versionOnShow,
} from "./scenes";
import { EMPTY_SENT_LIST, sentListFixture } from "./sentListFixture";

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

function triesNotice(inAd: number | null, onOffer = inAd !== null): Message {
  return {
    ...message([]),
    role: "notice",
    level: "info",
    tries: {
      scene: 2,
      in_ad: inAd,
      on_offer: onOffer,
      picked: 12,
      reason: "Each try spins.",
      clips: [
        { clip: 11, name: "First clip", version: "v1", url: "/t1.mp4", problem: "Floats." },
        { clip: 12, name: "Try 1", version: "v2", url: "/t2.mp4", problem: "Spins." },
        { clip: 13, name: "Try 2", version: "v3", url: "/t3.mp4", problem: "Spins." },
      ],
    },
  };
}

function cardVersion(number: number, more: Partial<CardVersion> = {}): CardVersion {
  return {
    number,
    label: `v${number}`,
    url: `/c${number}.mp4`,
    picture: null,
    verdict: "fail",
    problem: "",
    in_ad: false,
    free: true,
    ...more,
  };
}

test("a card plays the version previewed, else the one in the ad, else none it knows", () => {
  const versions = [cardVersion(1), cardVersion(2, { in_ad: true })];
  expect(versionOnShow(versions, 1)?.number).toBe(1);
  expect(versionOnShow(versions, null)?.number).toBe(2);
  // A preview of a version the scene no longer has falls back to the one in the ad.
  expect(versionOnShow(versions, 7)?.number).toBe(2);
  expect(versionOnShow([cardVersion(1)], null)).toBeNull();
});

test("no version passed when the check passed none of a scene's clips and failed one", () => {
  expect(noVersionPassed([cardVersion(1), cardVersion(2)])).toBe(true);
  expect(noVersionPassed([cardVersion(1), cardVersion(2, { verdict: "unsure" })])).toBe(true);
  expect(noVersionPassed([cardVersion(1), cardVersion(2, { verdict: "not_checked" })])).toBe(true);
  expect(noVersionPassed([cardVersion(1), cardVersion(2, { verdict: "pass" })])).toBe(false);
  // A scene the check never failed, as a talking one, isn't asked.
  expect(noVersionPassed([cardVersion(1, { verdict: "not_checked" })])).toBe(false);
  expect(noVersionPassed([])).toBe(false);
});

test("a clip that carries its sent list shows the pictures in sent order with their labels, and the pieces", () => {
  const [card] = sceneCards([message([{ ...clip(1, "/c1.mp4"), sent: sentListFixture() }])]);
  const sent = card.clips[0].sent;
  expect(sent?.pictures.map((one) => [one.image, one.url])).toEqual([
    [1, "/media/jobs/fixture/library/14.jpg"],
    [2, "/media/jobs/fixture/library/4.jpg"],
    [3, "/media/jobs/fixture/library/9.jpg"],
    [4, "/media/jobs/fixture/library/11.jpg"],
    [5, "/media/jobs/fixture/library/12.jpg"],
    [6, "/media/jobs/fixture/library/13.jpg"],
    [7, "/media/jobs/fixture/person-in-room.jpg"],
    [8, "/media/jobs/fixture/library/2.jpg"],
    [9, "/media/jobs/fixture/library/3.jpg"],
  ]);
  expect(sent?.pictures[1].label).toBe("A hand squeezing the press over a bowl.");
  expect(sent?.pieces).toEqual([
    { number: 1, url: "/media/jobs/fixture/pieces/video-1-moment-2.mp4", start: 6, end: 11 },
    { number: 2, url: "/media/jobs/fixture/pieces/video-2-moment-1.mp4", start: 3, end: 13 },
  ]);
});

test("a clip made before anything was recorded carries the empty list and shows nothing sent", () => {
  const [card] = sceneCards([message([{ ...clip(1, "/c1.mp4"), sent: EMPTY_SENT_LIST }])]);
  expect(card.clips[0].sent).toBeNull();
});

test("what was left out of a clip's pictures, and each recolour that fell back, by its label", () => {
  const [card] = sceneCards([message([{ ...clip(1, "/c1.mp4"), sent: sentListFixture() }])]);
  expect(card.clips[0].sent?.leftOut).toEqual(["The garlic press, closed, seen from the top."]);
  // Item 2 went as Image 8 in its own colour; item 1's recolour was done, so it says nothing.
  expect(card.clips[0].sent?.notRecoloured).toEqual([
    { label: "The garlic press, closed, seen from the back.", colour: "red" },
  ]);
});

test("after a give-up the scene's card shows the try kept in the ad and what it was sent", () => {
  // The kept try is never posted as a file of its own: the notice of every try carries it.
  const notice = triesNotice(13);
  notice.tries!.clips[2].sent = sentListFixture();
  const [card] = sceneCards([message([clip(2, "/t1.mp4")]), notice]);
  expect(card.clips.map((one) => one.file.url)).toEqual(["/t1.mp4", "/t3.mp4"]);
  expect(card.clips[1].sent?.pictures.map((one) => one.image)).toEqual(
    sentListFixture().pictures.map((one) => one.image),
  );
});

test("a tries notice whose try in the ad is already the scene's clip adds no clip", () => {
  const notice = triesNotice(11);
  const [card] = sceneCards([message([clip(2, "/t1.mp4")]), notice]);
  expect(card.clips.map((one) => one.file.url)).toEqual(["/t1.mp4"]);
});
