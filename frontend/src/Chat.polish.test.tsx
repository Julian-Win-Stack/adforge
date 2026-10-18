import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";
import { Chat } from "./Chat";
import type { Attachment, Message, PlanNotice, SceneStep } from "./api";

const NOW = "2026-10-18T10:00:00Z";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

/** One message as the server sends it; `seq` is filled in by `session`. */
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
    plan: null,
    ...more,
  };
}

function file(
  kind: Attachment["kind"],
  url: string,
  scene: number | null,
  version = "",
): Attachment {
  return { position: 1, kind, url, scene, version };
}

function started(scene: number, kind: SceneStep["kind"] = "starting_picture"): SceneStep {
  return { scene, kind, state: "started" };
}

function made(scene: number, kind: SceneStep["kind"]): SceneStep {
  return { scene, kind, state: "made" };
}

/** Opens a session whose server holds `messages`, numbered in order, and waits for them. */
async function session(messages: Omit<Message, "seq">[]) {
  const numbered = messages.map((message, index) => ({ ...message, seq: index + 1 }));
  vi.stubGlobal("fetch", async (url: string) => {
    const after = Number(new URL(url, "http://x").searchParams.get("after") ?? 0);
    return Response.json(numbered.filter((message) => message.seq > after));
  });
  render(
    <Chat
      sessionId="session-1"
      title="Shoes"
      sidebarOpen
      onShowSidebar={() => {}}
      onSent={() => {}}
    />,
  );
  // Every session here opens with the owner's words, so they on screen means the poll landed.
  await screen.findByText(numbered[0].text);
}

const PLAN: PlanNotice = {
  plan: 1,
  state: "proposed",
  question: "",
  estimate_usd: "0.40",
  estimate_up_to_usd: "0.80",
  cants: [],
  scenes: [
    {
      scene: 2,
      action: "new_clip",
      change: "He scrubs harder",
      line: "",
      overlay: "",
      go_back_to: null,
      cant: "",
      estimate_usd: "0.40",
      estimate_up_to_usd: "0.80",
      photos: [],
    },
  ],
};

test("a long problem notice shows its first line, and the rest only on asking", async () => {
  const listed = Array.from({ length: 59 }, (_, i) => `- https://shop.example/p${i}.jpg: gone.`);
  await session([
    said("user", "https://shop.example/shoes"),
    said("notice", ["59 photos picked off the page couldn't be kept.", ...listed].join("\n"), {
      level: "problem",
    }),
  ]);

  expect(screen.getByText("59 photos picked off the page couldn't be kept.")).toBeTruthy();
  expect(screen.queryByText(/p0\.jpg/)).toBeNull();

  await userEvent.click(screen.getByRole("button", { name: "Show details" }));
  expect(screen.getByText(/p58\.jpg/)).toBeTruthy();

  await userEvent.click(screen.getByRole("button", { name: "Hide details" }));
  expect(screen.queryByText(/p0\.jpg/)).toBeNull();
});

test("a one-line notice has nothing to show or hide", async () => {
  await session([said("user", "Make it"), said("notice", "Making scene 1's starting picture.")]);
  expect(screen.queryByRole("button", { name: "Show details" })).toBeNull();
});

test("while a plan waits for the owner's yes, the producer isn't said to be working", async () => {
  await session([
    said("user", "Scene 2: he should scrub harder"),
    said("notice", "", { plan: { id: 1, event: "shown", notice: PLAN } }),
  ]);
  await screen.findByRole("button", { name: "Yes, go ahead" });

  expect(screen.queryByText("The producer is working")).toBeNull();
  expect(screen.getByText("Waiting for your answer")).toBeTruthy();
});

test("while a suggested note waits for the owner, the producer isn't said to be working", async () => {
  await session([
    said("user", "Yes"),
    said("notice", "The check didn't see the change. Say go ahead to try the note below.", {
      suggested_note: { scene: 2, text: "He scrubs the sole harder" },
    }),
  ]);

  expect(screen.queryByText("The producer is working")).toBeNull();
  expect(screen.getByText("Waiting for your answer")).toBeTruthy();
});

test("the producer is said to be working while it makes what the owner asked for", async () => {
  await session([
    said("user", "https://shop.example/shoes"),
    said("notice", "Making scene 1's starting picture.", { step: started(1) }),
  ]);

  expect(screen.getByText("The producer is working")).toBeTruthy();
  expect(screen.queryByText("Waiting for your answer")).toBeNull();
});

test("the finished ad stands out as the owner's ad, ready to download", async () => {
  await session([
    said("user", "Make it"),
    said("agent", "Here's your ad.", { attachments: [file("video", "/media/ad.mp4", null)] }),
  ]);

  const ad = screen.getByRole("group", { name: "Your ad" });
  expect(within(ad).getByText("Your ad")).toBeTruthy();
  const download = within(ad).getByRole("link", { name: "Download" });
  expect(download.getAttribute("href")).toBe("/media/ad.mp4");
  expect(download.hasAttribute("download")).toBe(true);
});

test("a scene's picture and clip are labelled with the scene and version, and the clip's cover is the picture", async () => {
  await session([
    said("user", "Make it"),
    said("agent", "", {
      attachments: [file("picture", "/media/s3-v1.png", 3, "v1")],
      step: made(3, "starting_picture"),
    }),
    said("agent", "", {
      attachments: [file("picture", "/media/s3-v2.png", 3, "v2 black toilet")],
      step: made(3, "starting_picture"),
    }),
    said("agent", "Scene 3 is done.", {
      attachments: [file("video", "/media/s3.mp4", 3, "v2 black toilet")],
      step: made(3, "clip"),
    }),
  ]);

  expect(screen.getByText("Scene 3 · v1")).toBeTruthy();
  const clip = screen.getByLabelText("Video the agent made");
  expect(clip.closest("figure")?.textContent).toBe("Scene 3 · v2 black toilet");
  expect(clip.getAttribute("poster")).toBe("/media/s3-v2.png");
  expect(screen.getAllByText("Scene 3 · v2 black toilet")).toHaveLength(2);
  expect(screen.queryByRole("group", { name: "Your ad" })).toBeNull();
});

test("an old and a new clip of a scene shown together each carry their version", async () => {
  await session([
    said("user", "Yes"),
    said("agent", "The check didn't see the change. Old and new:", {
      attachments: [
        { ...file("video", "/media/s2-v1.mp4", 2, "v1"), position: 1 },
        { ...file("video", "/media/s2-v2.mp4", 2, "v2 scrubs harder"), position: 2 },
      ],
    }),
  ]);

  expect(screen.getByText("Scene 2 · v1")).toBeTruthy();
  expect(screen.getByText("Scene 2 · v2 scrubs harder")).toBeTruthy();
});

test("back-to-back notices that pictures started read as one line", async () => {
  await session([
    said("user", "Make it"),
    ...[1, 2, 3, 4, 5].map((scene) =>
      said("notice", `Making scene ${scene}'s starting picture.`, { step: started(scene) }),
    ),
    said("agent", "", {
      attachments: [file("picture", "/media/s1.png", 1, "v1")],
      step: made(1, "starting_picture"),
    }),
    said("notice", "Remaking scene 2's starting picture.", { step: started(2) }),
    said("notice", "Remaking scene 4's starting picture.", { step: started(4) }),
    said("notice", "Making scene 1's clip.", { step: started(1, "clip") }),
  ]);

  expect(screen.getByText("Making pictures for scenes 1-5")).toBeTruthy();
  expect(screen.queryByText("Making scene 3's starting picture.")).toBeNull();
  expect(screen.getByText("Remaking pictures for scenes 2, 4")).toBeTruthy();
  expect(screen.getByText("Making scene 1's clip.")).toBeTruthy();
});

test("once a session has started, the message box asks for a message, not a product link", async () => {
  await session([said("user", "https://shop.example/shoes"), said("agent", "On it.")]);
  expect(screen.getByRole("textbox", { name: "Message" }).getAttribute("placeholder")).toBe(
    "Write a message or answer the producer...",
  );
});
