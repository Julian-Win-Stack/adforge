import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { Chat } from "./Chat";
import type { Attachment, CardVersion, Message, SceneStep } from "./api";

// Spec #729: the newest finished ad lists its scenes beside the player. A tap on a scene
// plays that scene and opens its note; the notes are fixed together from under the ad.

const NOW = "2026-11-19T10:00:00Z";

type Sent = { text: string; photos: string[]; photoScenes: number[] };

let played: { from: number; paused: boolean }[] = [];

beforeEach(() => {
  played = [];
  // jsdom plays nothing: a play is noted with where the player stood.
  vi.spyOn(HTMLMediaElement.prototype, "play").mockImplementation(function (
    this: HTMLMediaElement,
  ) {
    played.push({ from: this.currentTime, paused: false });
    return Promise.resolve();
  });
  vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(() => {});
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function said(
  role: Message["role"],
  text: string,
  more: Partial<Omit<Message, "seq" | "role" | "text">> = {},
): Omit<Message, "seq"> {
  return {
    role,
    text,
    level: role === "notice" ? "info" : "",
    created_at: NOW,
    attachments: [],
    step: null,
    suggested_note: null,
    ...more,
  };
}

function file(kind: Attachment["kind"], url: string, scene: number | null): Attachment {
  return { position: 1, kind, url, scene, version: "" };
}

function step(scene: number, state: SceneStep["state"]): SceneStep {
  return { scene, kind: "clip", state };
}

const TIMES = [
  { scene: 1, start: 0, end: 4.5 },
  { scene: 2, start: 4.5, end: 8 },
  { scene: 3, start: 8, end: 15.25 },
];

/** Three scenes, each with its picture and clip, then the ad made from them. */
function madeAd(times: Attachment["scene_times"] = TIMES, url = "/media/ad.mp4") {
  return [
    said("user", "https://shop.example/mug"),
    ...[1, 2, 3].flatMap((n) => [
      said("agent", "", { attachments: [file("picture", `/media/p${n}.png`, n)] }),
      said("agent", "", {
        attachments: [file("video", `/media/c${n}.mp4`, n)],
        step: step(n, "made"),
      }),
    ]),
    said("agent", "Here's your ad.", {
      attachments: [{ ...file("video", url, null), scene_times: times }],
    }),
  ];
}

function version(scene: number, number: number, more: Partial<CardVersion> = {}): CardVersion {
  return {
    number,
    label: `v${number}`,
    url: `/media/c${scene}-v${number}.mp4`,
    picture: `/media/p${scene}-v${number}.png`,
    verdict: "pass",
    problem: "",
    in_ad: number === 1,
    free: true,
    ...more,
  };
}

const WORDS: Record<number, Partial<CardVersion>> = {
  1: { line: "Meet the mug that keeps it hot." },
  2: { shows: "Coffee poured into the mug on a kitchen counter" },
  3: { line: "Get yours today.", shows: "The mug on a shelf" },
};

/** Opens a session whose server holds `messages` and each scene's `versions`, and waits for
 * the conversation. Gives what the owner sends and swaps. */
async function session(
  messages: Omit<Message, "seq">[],
  versions: Record<number, CardVersion[]> = Object.fromEntries(
    [1, 2, 3].map((n) => [n, [version(n, 1, WORDS[n])]]),
  ),
) {
  const numbered = messages.map((message, index) => ({ ...message, seq: index + 1 }));
  const sent: Sent[] = [];
  const used: { scene: number; version: number }[] = [];
  vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
    const path = new URL(url, "http://x");
    const use = path.pathname.match(/\/scenes\/(\d+)\/use\/$/);
    if (use) {
      const scene = Number(use[1]);
      const { version: number } = JSON.parse(String(init?.body));
      used.push({ scene, version: number });
      versions[scene] = versions[scene].map((one) => ({ ...one, in_ad: one.number === number }));
      return Response.json({ scene, versions: versions[scene] });
    }
    if (path.pathname.endsWith("/scenes/")) {
      return Response.json(
        Object.entries(versions).map(([scene, list]) => ({ scene: Number(scene), versions: list })),
      );
    }
    if (init?.method === "POST") {
      const form = init.body instanceof FormData ? init.body : null;
      const text = form ? String(form.get("text")) : JSON.parse(String(init.body)).text;
      sent.push({
        text,
        photos: form ? form.getAll("photos").map((one) => (one as File).name) : [],
        photoScenes: form ? form.getAll("photo_scenes").map(Number) : [],
      });
      const message = { ...said("user", text), seq: numbered.length + sent.length };
      return Response.json({ session: { id: "session-1", name: "Mug" }, message }, { status: 201 });
    }
    const after = Number(path.searchParams.get("after") ?? 0);
    return Response.json(numbered.filter((message) => message.seq > after));
  });
  render(
    <Chat
      sessionId="session-1"
      title="Mug"
      sidebarOpen
      onShowSidebar={() => {}}
      onSent={() => {}}
    />,
  );
  await screen.findByText(numbered[0].text);
  return { sent, used, user: userEvent.setup() };
}

function ad() {
  return screen.getByRole("group", { name: "Your ad" });
}

function scenes() {
  return within(ad()).getByRole("list", { name: "Scenes" });
}

function row(scene: number) {
  return within(scenes()).getByRole("button", { name: new RegExp(`^Scene ${scene}\\b`) });
}

function player() {
  return within(ad()).getByLabelText("Video the agent made") as HTMLVideoElement;
}

function fixButton() {
  return within(ad()).getByRole("button", { name: /^Fix( \d+)? scenes?$/ });
}

test("the finished ad lists its scenes in play order, with where each plays and what it says", async () => {
  await session(madeAd());

  const rows = within(scenes()).getAllByRole("listitem");
  expect(rows).toHaveLength(3);
  expect(rows[0].textContent).toContain("Scene 1");
  expect(rows[0].textContent).toContain("0:00–0:04");
  expect(rows[2].textContent).toContain("0:08–0:15");
  // Its line in quotes, else what it shows: the version in the ad's.
  await waitFor(() => expect(rows[0].textContent).toContain("“Meet the mug that keeps it hot.”"));
  expect(rows[0].querySelector("img")?.getAttribute("src")).toBe("/media/p1-v1.png");
  expect(rows[1].textContent).toContain("Coffee poured into the mug on a kitchen counter");
  expect(rows[2].textContent).toContain("“Get yours today.”");
  expect(rows[2].textContent).not.toContain("The mug on a shelf");
  expect(
    within(ad()).getByText("Something off? Tap the scene and write what to change."),
  ).toBeTruthy();
  // The time bar under the player has a part per scene, as wide as the scene is long.
  const bar = within(ad()).getByRole("group", { name: "The ad's scenes" });
  const parts = within(bar).getAllByRole("button");
  expect(parts.map((part) => part.getAttribute("aria-label"))).toEqual([
    "Scene 1, 0:00 to 0:04",
    "Scene 2, 0:04 to 0:08",
    "Scene 3, 0:08 to 0:15",
  ]);
  expect(parts.map((part) => part.style.flexGrow)).toEqual(["4.5", "3.5", "7.25"]);
});

test("tapping a scene plays that scene of the ad, stops at its end and opens its note", async () => {
  const { user } = await session(madeAd());

  await user.click(row(2));

  expect(player().currentTime).toBeCloseTo(4.5, 1);
  expect(played).toHaveLength(1);
  const box = within(scenes()).getByRole("textbox", { name: "Note for scene 2" });
  expect(box.getAttribute("placeholder")).toBe("What should change in this scene?");
  expect(within(scenes()).queryByRole("textbox", { name: "Note for scene 1" })).toBeNull();

  // Played to the scene's end, it stops there.
  const pause = vi.mocked(HTMLMediaElement.prototype.pause);
  player().currentTime = 7.99;
  fireEvent.timeUpdate(player());
  expect(pause).toHaveBeenCalled();

  // Its part of the bar does the same.
  const bar = within(ad()).getByRole("group", { name: "The ad's scenes" });
  await user.click(within(bar).getByRole("button", { name: /^Scene 3,/ }));
  expect(player().currentTime).toBeCloseTo(8, 1);
  expect(within(scenes()).getByRole("textbox", { name: "Note for scene 3" })).toBeTruthy();
});

test("the scene playing is marked while the ad plays", async () => {
  await session(madeAd());

  player().currentTime = 5;
  fireEvent.play(player());
  fireEvent.timeUpdate(player());

  const rows = within(scenes()).getAllByRole("listitem");
  await waitFor(() => expect(rows[1].classList.contains("playing")).toBe(true));
  expect(rows[0].classList.contains("playing")).toBe(false);

  fireEvent.pause(player());
  await waitFor(() => expect(rows[1].classList.contains("playing")).toBe(false));
});

test("notes on two scenes go as one message in scene order, each photo tagged with its scene", async () => {
  const { sent, user } = await session(madeAd());
  expect(fixButton().hasAttribute("disabled")).toBe(true);
  expect(fixButton().textContent).toBe("Fix scenes");

  await user.click(row(3));
  await user.type(
    within(scenes()).getByRole("textbox", { name: "Note for scene 3" }),
    "Bigger logo",
  );
  const shelf = new File(["x"], "shelf.png", { type: "image/png" });
  await user.upload(within(scenes()).getByLabelText("Photos or videos for scene 3"), shelf);
  await user.click(row(1));
  await user.type(
    within(scenes()).getByRole("textbox", { name: "Note for scene 1" }),
    "Warmer light",
  );

  // Each noted scene is marked, on its row and on its part of the bar.
  expect(row(3).textContent).toContain("Note");
  const bar = within(ad()).getByRole("group", { name: "The ad's scenes" });
  expect(
    within(bar)
      .getByRole("button", { name: /^Scene 3,/ })
      .classList.contains("noted"),
  ).toBe(true);
  expect(within(ad()).getByText(/Write notes on as many scenes as you like/).textContent).toBe(
    "Write notes on as many scenes as you like, then fix them together. The other scenes stay as they are.",
  );
  expect(fixButton().textContent).toBe("Fix 2 scenes");

  await user.click(fixButton());

  await waitFor(() => expect(sent).toHaveLength(1));
  expect(sent[0]).toEqual({
    text: "Scene 1: Warmer light\nScene 3: Bigger logo",
    photos: ["shelf.png"],
    photoScenes: [3],
  });
  await waitFor(() => expect(fixButton().textContent).toBe("Fix scenes"));
});

test("a photo with no words in its note can't be fixed, and the card says what to write", async () => {
  const { user } = await session(madeAd());
  await user.click(row(2));
  await user.upload(
    within(scenes()).getByLabelText("Photos or videos for scene 2"),
    new File(["x"], "cup.png", { type: "image/png" }),
  );

  expect(within(ad()).getByText("Say in scene 2's note what to take from its photo.")).toBeTruthy();
  expect(fixButton().hasAttribute("disabled")).toBe(true);
});

test("the right panel shows while the ad is made, hides once it's done, and shows again while a scene is remade", async () => {
  const making = madeAd().slice(0, -1);
  await session(making);
  const panel = await screen.findByRole("region", { name: "Scenes" });
  expect(within(panel).getByText("Your ad, scene by scene")).toBeTruthy();
  expect(
    within(panel).getByText("Something off? Write what to change under that scene."),
  ).toBeTruthy();
  expect(within(panel).getByRole("button", { name: "Fix scenes" })).toBeTruthy();
  expect(
    within(panel).getByRole("textbox", { name: "Note for scene 1" }).getAttribute("placeholder"),
  ).toBe("What should change in this scene?");
  cleanup();

  await session(madeAd());
  await screen.findByRole("group", { name: "Your ad" });
  expect(screen.queryByRole("region", { name: "Scenes" })).toBeNull();
  cleanup();

  await session([
    ...madeAd(),
    said("user", "Scene 2: Warmer light"),
    said("notice", "Remaking scene 2's clip.", { step: step(2, "started") }),
  ]);
  expect(await screen.findByRole("region", { name: "Scenes" })).toBeTruthy();
  expect(row(2).textContent).toContain("Being fixed");
});

test("an ad made before scene times were kept still lists its scenes and opens their notes", async () => {
  const { user } = await session(madeAd(null));

  expect(within(scenes()).getAllByRole("listitem")).toHaveLength(3);
  expect(within(ad()).queryByRole("group", { name: "The ad's scenes" })).toBeNull();
  expect(scenes().textContent).not.toMatch(/\d:\d\d–/);

  await user.click(row(1));

  expect(within(scenes()).getByRole("textbox", { name: "Note for scene 1" })).toBeTruthy();
  expect(played).toHaveLength(0);
});

test("only the newest ad lists its scenes; an older one is a plain player", async () => {
  const first = madeAd();
  await session([
    ...first,
    said("agent", "Here's the new one.", {
      attachments: [{ ...file("video", "/media/ad-2.mp4", null), scene_times: TIMES }],
    }),
  ]);
  await screen.findByText("Here's the new one.");

  const ads = screen.getAllByRole("group", { name: "Your ad" });
  expect(ads).toHaveLength(2);
  expect(within(ads[0]).queryByRole("list", { name: "Scenes" })).toBeNull();
  expect(within(ads[0]).getByRole("link", { name: "Download" })).toBeTruthy();
  expect(within(ads[1]).getByRole("list", { name: "Scenes" })).toBeTruthy();
});

test("an opened scene with several versions swaps one in for free, and Undo swaps back", async () => {
  const { used, user } = await session(madeAd(), {
    1: [version(1, 1)],
    2: [version(2, 1, { verdict: "fail" }), version(2, 2, { in_ad: false, verdict: "fail" })],
    3: [version(3, 1)],
  });
  await user.click(row(2));
  const strip = await within(scenes()).findByRole("list", { name: "Scene 2's clips" });
  expect(
    within(scenes()).getByText("No version passed the check. Pick the best one."),
  ).toBeTruthy();

  await user.click(within(strip).getByRole("button", { name: /^v2/ }));
  // The version previews in the row itself.
  expect(within(scenes()).getByLabelText("Scene 2's v2")).toBeTruthy();
  await user.click(within(scenes()).getByRole("button", { name: "Use this in the ad" }));

  await within(scenes()).findByText("v2 is in the ad now");
  expect(used).toEqual([{ scene: 2, version: 2 }]);

  await user.click(within(scenes()).getByRole("button", { name: "Undo" }));
  await waitFor(() => expect(used).toHaveLength(2));
  expect(used[1]).toEqual({ scene: 2, version: 1 });
});

test("a version that needs refilming can be asked for from the opened row", async () => {
  const { user } = await session(madeAd(), {
    1: [version(1, 1)],
    2: [version(2, 1), version(2, 2, { in_ad: false, free: false, refilm: "The voice changed." })],
    3: [version(3, 1)],
  });
  await user.click(row(2));
  const strip = await within(scenes()).findByRole("list", { name: "Scene 2's clips" });
  await user.click(within(strip).getByRole("button", { name: /^v2/ }));

  expect(within(scenes()).getByText("The voice changed.")).toBeTruthy();
  await user.click(within(scenes()).getByRole("button", { name: "Ask to refilm this version" }));

  const box = within(scenes()).getByRole("textbox", { name: "Note for scene 2" });
  expect((box as HTMLTextAreaElement).value).toBe("Please refilm v2 of scene 2.");
  expect(fixButton().textContent).toBe("Fix 1 scene");
});

test("a suggested note fills its scene's note under the ad", async () => {
  const { user } = await session([
    ...madeAd(),
    said("notice", "The check didn't see the change.", {
      suggested_note: { scene: 3, text: "Show the logo bigger" },
    }),
  ]);
  await user.click(row(3));

  const box = within(scenes()).getByRole("textbox", { name: "Note for scene 3" });
  expect((box as HTMLTextAreaElement).value).toBe("Show the logo bigger");
  expect(within(scenes()).getByText("Suggested")).toBeTruthy();
});
