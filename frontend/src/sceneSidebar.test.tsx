import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";
import { ScenePanel } from "./ScenePanel";
import { useSceneWork } from "./sceneWork";
import { SessionList, sessionTitle } from "./SessionList";
import type { Attachment, CardVersion, Message, SentList, Session } from "./api";
import { EMPTY_SENT_LIST, sentListFixture } from "./sentListFixture";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

let seq = 0;

function said(
  attachments: Attachment[],
  suggested_note: Message["suggested_note"] = null,
  role: Message["role"] = "agent",
): Message {
  seq += 1;
  return {
    seq,
    role,
    level: "",
    text: "",
    created_at: "2026-10-18T10:00:00Z",
    attachments,
    step: null,
    suggested_note,
  };
}

function file(kind: Attachment["kind"], url: string, scene: number | null, version = "") {
  return { position: 1, kind, url, scene, version };
}

/** Two scenes with a picture and a clip each, made from those pictures. */
const SCENES = [
  said([file("picture", "/p1.png", 1)]),
  said([file("video", "/c1.mp4", 1, "v1")]),
  said([file("picture", "/p2.png", 2)]),
  said([file("video", "/c2.mp4", 2, "v1")]),
];
const FINISHED_AD = said([file("video", "/ad.mp4", null)]);

/** The panel as the chat holds it, its notes and clips kept beside it. */
function Panel({ sessionId, messages }: { sessionId: string | null; messages: Message[] }) {
  const work = useSceneWork({ sessionId, messages, watch: null, onSend: async () => {} });
  return <ScenePanel messages={messages} work={work} />;
}

function showPanel(messages: Message[], width = 1400) {
  vi.stubGlobal("innerWidth", width);
  return render(<Panel sessionId={null} messages={messages} />);
}

function toggle() {
  return screen.getByRole("button", { name: /scene by scene/ });
}

test("on a phone, the scene cards start folded once the ad is finished, with each scene's picture small in the bar", async () => {
  showPanel([...SCENES, FINISHED_AD], 390);

  expect(toggle().getAttribute("aria-expanded")).toBe("false");
  expect(screen.queryAllByRole("article")).toEqual([]);
  const thumbs = [...toggle().querySelectorAll("img")].map((one) => one.getAttribute("src"));
  expect(thumbs).toEqual(["/p1.png", "/p2.png"]);

  await userEvent.click(toggle());
  expect(screen.getAllByRole("article").map((card) => card.getAttribute("aria-label"))).toEqual([
    "Scene 1",
    "Scene 2",
  ]);
});

test("on a phone, the scene cards stay open while the ad is still being made, a fix check's clips included", () => {
  // The fix check shows the old and the new clip without their scene, beside its suggestion.
  const fixCheck = said(
    [file("video", "/c1.mp4", null), file("video", "/c1-b.mp4", null)],
    { scene: 1, text: "Show the brush touching the shoe" },
    "notice",
  );
  showPanel([...SCENES, fixCheck], 390);
  expect(toggle().getAttribute("aria-expanded")).toBe("true");
  expect(screen.getAllByRole("article")).toHaveLength(2);
});

test("on a phone, the ad landing while the owner writes a note leaves the cards open", async () => {
  const shown = showPanel(SCENES, 390);
  await userEvent.type(screen.getByRole("textbox", { name: "Note for scene 1" }), "Brighter");
  shown.rerender(<Panel sessionId={null} messages={[...SCENES, FINISHED_AD]} />);
  const box = screen.getByRole("textbox", { name: "Note for scene 1" }) as HTMLTextAreaElement;
  expect(box.value).toBe("Brighter");
  expect(document.activeElement).toBe(box);
});

test("on a wide screen, the scene cards stay open after the ad is finished", () => {
  showPanel([...SCENES, FINISHED_AD]);
  expect(toggle().getAttribute("aria-expanded")).toBe("true");
});

test("each clip has a dedicated fullscreen button even in a narrow sidebar", async () => {
  showPanel(SCENES);
  const card = screen.getByRole("article", { name: "Scene 2" });
  const player = within(card).getByLabelText("Scene 2's clip").parentElement!;
  const request = vi.fn().mockResolvedValue(undefined);
  player.requestFullscreen = request;

  await userEvent.click(within(card).getByRole("button", { name: "View scene 2 fullscreen" }));

  expect(request).toHaveBeenCalledOnce();
});

test("the sidebar carries each version's own timed captions when the owner previews another clip", async () => {
  const versions: CardVersion[] = [
    {
      number: 1,
      label: "v1 first version",
      url: "/c1.mp4",
      picture: "/p1.png",
      verdict: "pass",
      problem: "",
      in_ad: false,
      free: true,
      captions: [{ text: "First <mug> & lid.", start: 0.125, end: 2.75 }],
    },
    {
      number: 2,
      label: "v2 brighter",
      url: "/c1-new.mp4",
      picture: "/p1.png",
      verdict: "pass",
      problem: "",
      in_ad: true,
      free: false,
      captions: [{ text: "The new mug.", start: 1, end: 3.5 }],
    },
  ];
  vi.stubGlobal("fetch", async () => new Response(JSON.stringify([{ scene: 1, versions }])));
  render(<Panel sessionId="one" messages={SCENES} />);
  const card = screen.getByRole("article", { name: "Scene 1" });
  await within(card).findByText("v2 brighter");
  const current = within(card).getByLabelText("Scene 1's clip");
  const track = current.querySelector("track")!;
  expect(track.default).toBe(true);
  expect(decodeURIComponent(track.src)).toContain("00:00:01.000 --> 00:00:03.500");
  expect(decodeURIComponent(track.src)).toContain("The new mug.");

  await userEvent.click(within(card).getByRole("button", { name: "v1, passed the check" }));

  const previous = within(card).getByLabelText("Scene 1's v1");
  expect(previous.getAttribute("src")).toBe("/c1.mp4");
  const previousTrack = decodeURIComponent(previous.querySelector("track")!.src);
  expect(previousTrack).toContain("00:00:00.125 --> 00:00:02.750");
  expect(previousTrack).toContain("First &lt;mug&gt; &amp; lid.");
  expect(previousTrack).not.toContain("The new mug.");
});

test("the folded bar shows no pictures while the cards are open", () => {
  showPanel(SCENES);
  expect(toggle().querySelectorAll("img")).toHaveLength(0);
});

test("dragging the left border right closes the scenes and reopening keeps the owner's note and photo", async () => {
  showPanel(SCENES);
  const user = userEvent.setup();
  await user.type(screen.getByRole("textbox", { name: "Note for scene 1" }), "Brighter");
  const photo = new File(["photo"], "reference.png", { type: "image/png" });
  const card = within(screen.getByRole("article", { name: "Scene 1" }));
  vi.stubGlobal(
    "URL",
    class extends URL {
      static createObjectURL = () => "blob:reference";
      static revokeObjectURL = () => {};
    },
  );
  await user.upload(card.getByLabelText("Photos or videos for scene 1"), photo);
  const border = screen.getByRole("button", { name: "Close scenes" });
  // jsdom has no pointer capture; real capture is exercised in the browser too.
  border.setPointerCapture = vi.fn();
  await user.pointer([
    { target: border, keys: "[MouseLeft>]", coords: { clientX: 1000 } },
    { coords: { clientX: 1140 } },
    { keys: "[/MouseLeft]", coords: { clientX: 1140 } },
  ]);
  expect(toggle().getAttribute("aria-expanded")).toBe("false");
  expect(screen.queryAllByRole("article")).toEqual([]);

  await user.click(toggle());
  expect(
    (screen.getByRole("textbox", { name: "Note for scene 1" }) as HTMLTextAreaElement).value,
  ).toBe("Brighter");
  expect(screen.getByRole("button", { name: /Remove reference.png/ })).toBeTruthy();
});

test("short, leftward, and cancelled border drags leave the scenes open", async () => {
  showPanel(SCENES);
  const user = userEvent.setup();
  const border = screen.getByRole("button", { name: "Close scenes" });
  border.setPointerCapture = vi.fn();
  for (const end of [1020, 850]) {
    await user.pointer([
      { target: border, keys: "[MouseLeft>]", coords: { clientX: 1000 } },
      { coords: { clientX: end } },
      { keys: "[/MouseLeft]", coords: { clientX: end } },
    ]);
    expect(toggle().getAttribute("aria-expanded")).toBe("true");
  }
  await user.pointer([
    { target: border, keys: "[MouseLeft>]", coords: { clientX: 1000 } },
    { coords: { clientX: 1140 } },
  ]);
  fireEvent.pointerCancel(border);
  await user.pointer({ keys: "[/MouseLeft]", coords: { clientX: 1140 } });
  expect(toggle().getAttribute("aria-expanded")).toBe("true");

  border.blur();
  await user.pointer([
    { target: border, keys: "[MouseLeft>]", coords: { clientX: 1000 } },
    { coords: { clientX: 1140 } },
  ]);
  await user.keyboard("{Escape}");
  await user.pointer({ keys: "[/MouseLeft]", coords: { clientX: 1140 } });
  expect(toggle().getAttribute("aria-expanded")).toBe("true");
});

test("the border also closes scenes from the keyboard", async () => {
  showPanel(SCENES);
  screen.getByRole("button", { name: "Close scenes" }).focus();
  await userEvent.keyboard("{Enter}");
  expect(toggle().getAttribute("aria-expanded")).toBe("false");
});

test("a long version name stays on one line, its full name a hover away", () => {
  const long = "v2 He should actually be holding the brush in his left hand";
  showPanel([
    said([file("video", "/c1-a.mp4", 1, "v1 first version")]),
    said([file("video", "/c1.mp4", 1, long)]),
  ]);
  const label = within(screen.getByRole("article", { name: "Scene 1" })).getByText(long);
  expect(label.getAttribute("title")).toBe(long);
});

test("the note box grows to fit what is typed", async () => {
  vi.spyOn(HTMLTextAreaElement.prototype, "scrollHeight", "get").mockImplementation(function (
    this: HTMLTextAreaElement,
  ) {
    // Like a browser: a box never measures smaller than it is drawn.
    const drawn = parseInt(this.style.getPropertyValue("--note-height")) || 0;
    return Math.max(20 + 18 * this.value.split("\n").length, drawn);
  });
  showPanel(SCENES);
  const box = screen.getByRole("textbox", { name: "Note for scene 1" }) as HTMLTextAreaElement;

  await userEvent.type(box, "one{Enter}two{Enter}three{Enter}four");
  expect(box.style.getPropertyValue("--note-height")).toBe("92px");

  await userEvent.type(box, "{Backspace}".repeat("\nthree\nfour".length));
  expect(box.value).toBe("one\ntwo");
  expect(box.style.getPropertyValue("--note-height")).toBe("56px");
});

test("a note the agent suggested is marked as suggested, until the owner writes their own", async () => {
  showPanel([...SCENES, said([], { scene: 2, text: "Show the brush touching the shoe" })]);
  const card = screen.getByRole("article", { name: "Scene 2" });
  const box = within(card).getByRole("textbox", {
    name: "Note for scene 2",
  }) as HTMLTextAreaElement;
  expect(box.value).toBe("Show the brush touching the shoe");
  expect(within(card).getByText("Suggested")).toBeTruthy();
  expect(
    within(screen.getByRole("article", { name: "Scene 1" })).queryByText("Suggested"),
  ).toBeNull();

  await userEvent.type(box, " please");
  expect(within(card).queryByText("Suggested")).toBeNull();
});

function sentClip(scene: number, sent: SentList) {
  return { ...file("video", `/c${scene}.mp4`, scene, "v1"), sent };
}

test("a clip's card shows nothing of what it was sent: its starting picture stays its poster", () => {
  showPanel([said([file("picture", "/p1.png", 1)]), said([sentClip(1, sentListFixture())])]);
  const card = screen.getByRole("article", { name: "Scene 1" });
  expect(within(card).getByLabelText("Scene 1's clip").getAttribute("poster")).toBe("/p1.png");
  expect(within(card).queryByRole("list", { name: "Pictures sent" })).toBeNull();
  expect(within(card).queryByRole("list", { name: "Video pieces sent" })).toBeNull();
  expect(within(card).queryByText(/Left out/)).toBeNull();
});

test("a clip with the empty sent list looks as it always did", () => {
  showPanel([said([file("picture", "/p1.png", 1)]), said([sentClip(1, EMPTY_SENT_LIST)])]);
  const card = screen.getByRole("article", { name: "Scene 1" });
  expect(within(card).queryByRole("list")).toBeNull();
  expect(within(card).queryByText(/Left out/)).toBeNull();
  expect(card.querySelector("video")?.getAttribute("poster")).toBe("/p1.png");
});

test("a clip with no starting picture gets the first picture it was sent as its poster", () => {
  showPanel([said([sentClip(1, sentListFixture())])]);
  const clip = screen.getByLabelText("Scene 1's clip");
  expect(clip.getAttribute("poster")).toBe("/media/jobs/fixture/library/14.jpg");
});

function session(name: string): Session {
  return { id: name, name, created_at: new Date().toISOString() };
}

test("a session named after a product link shows the product and the shop", () => {
  expect(
    sessionTitle(session("https://www.allbirds.com/products/mens-tree-runners?variant=4")),
  ).toBe("Mens Tree Runners · allbirds.com");
  expect(
    sessionTitle(
      session("https://www.amazon.com/Bamboo-Toothbrush-Pack/dp/B07X4Q2ABC make it fun"),
    ),
  ).toBe("Bamboo Toothbrush Pack · amazon.com");
  expect(sessionTitle(session("www.glossier.com/products/balm-dotcom.html"))).toBe(
    "Balm Dotcom · glossier.com",
  );
  expect(sessionTitle(session("https://shop.example.com/"))).toBe("shop.example.com");
  expect(
    sessionTitle(
      session("https://www.amazon.com/Bamboo-Toothbrush-Pack/dp/B07X4Q2ABC/ref=sr_1_5?k=brush"),
    ),
  ).toBe("Bamboo Toothbrush Pack · amazon.com");
  expect(sessionTitle(session("https://www.target.com/p/ninja-blender/-/A-87654321"))).toBe(
    "Ninja Blender · target.com",
  );
  expect(sessionTitle(session("https://www.ebay.com/itm/123456789"))).toBe("ebay.com");
  expect(sessionTitle(session("https://www.etsy.com/listing/123456/handmade-clay-mug"))).toBe(
    "Handmade Clay Mug · etsy.com",
  );
  expect(sessionTitle(session("https://müller.de/produkte/bürste"))).toBe("Bürste · müller.de");
});

test("a link the server cut short loses the cut word", () => {
  expect(
    sessionTitle(session("https://www.allbirds.com/products/womens-tree-runner-blizzard-sol…")),
  ).toBe("Womens Tree Runner Blizzard · allbirds.com");
  expect(sessionTitle(session("https://www.amazon.com/B…"))).toBe("amazon.com");
});

test("a session the owner named, or named from words, keeps its name", () => {
  expect(sessionTitle(session("Summer sneaker ad"))).toBe("Summer sneaker ad");
  expect(sessionTitle(session("Make me an ad for my mug"))).toBe("Make me an ad for my mug");
  expect(sessionTitle(session(""))).toBe("Untitled");
});

test("the sidebar lists sessions named after a link by their product", () => {
  render(
    <SessionList
      sessions={[session("https://www.allbirds.com/products/mens-tree-runners")]}
      folders={[]}
      openId={null}
      onOpen={() => {}}
      onNew={() => {}}
      onSessionSaved={() => {}}
      onCopied={() => {}}
      onFolderSaved={() => {}}
      onFolderRemoved={() => {}}
      onHide={() => {}}
    />,
  );
  expect(screen.getByRole("button", { name: "Mens Tree Runners · allbirds.com" })).toBeTruthy();
});

const QUESTION = "Could you send a short video of the lid being screwed onto the mug?";

/** Code's red line that the fixing agent asked the shop owner a question about scene 2. */
function asked(open = true): Message {
  return {
    ...said([]),
    role: "notice",
    level: "problem",
    text: "The fixing agent has a question for you about scene 2's clip. The question is on scene 2's card.",
    step: { scene: 2, kind: "clip", state: "made" },
    question: { scene: 2, text: QUESTION, open },
  };
}

test.each([true, false])("old scene questions never appear on the card (open: %s)", (open) => {
  showPanel([...SCENES, asked(open)]);
  const card = screen.getByRole("article", { name: "Scene 2" });
  expect(within(card).queryByRole("note", { name: "Question for you" })).toBeNull();
  expect(within(card).queryByText(QUESTION)).toBeNull();
  expect(within(card).getByRole("textbox", { name: "Note for scene 2" })).toBeTruthy();
});
