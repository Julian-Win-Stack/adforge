import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";
import { ScenePanel } from "./ScenePanel";
import { SessionList, sessionTitle } from "./SessionList";
import type { Attachment, Message, Session } from "./api";

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
    plan: null,
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

function showPanel(messages: Message[], width = 1400) {
  vi.stubGlobal("innerWidth", width);
  return render(<ScenePanel messages={messages} onSend={async () => {}} />);
}

function toggle() {
  return screen.getByRole("button", { name: /Scenes/ });
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
  shown.rerender(<ScenePanel messages={[...SCENES, FINISHED_AD]} onSend={async () => {}} />);
  const box = screen.getByRole("textbox", { name: "Note for scene 1" }) as HTMLTextAreaElement;
  expect(box.value).toBe("Brighter");
  expect(document.activeElement).toBe(box);
});

test("on a wide screen, the scene cards stay open after the ad is finished", () => {
  showPanel([...SCENES, FINISHED_AD]);
  expect(toggle().getAttribute("aria-expanded")).toBe("true");
});

test("the folded bar shows no pictures while the cards are open", () => {
  showPanel(SCENES);
  expect(toggle().querySelectorAll("img")).toHaveLength(0);
});

test("a long version name stays on one line, its full name a hover away", () => {
  const long = "v2 He should actually be holding the brush in his left hand";
  showPanel([said([file("video", "/c1.mp4", 1, long)])]);
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

test("a note the producer suggested is marked as suggested, until the owner writes their own", async () => {
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
      openId={null}
      onOpen={() => {}}
      onNew={() => {}}
      onRenamed={() => {}}
      onHide={() => {}}
    />,
  );
  expect(screen.getByRole("button", { name: "Mens Tree Runners · allbirds.com" })).toBeTruthy();
});
