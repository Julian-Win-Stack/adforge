import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";
import { Chat } from "./Chat";
import type { Attachment, Message, SceneStep } from "./api";

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
  expect(screen.getByText("Making scene 1's starting picture.")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Show details" })).toBeNull();
});

test("the producer's answer to the owner's notes brings no plan card and no yes button", async () => {
  await session([
    said("user", "Scene 2: he should scrub harder"),
    said("agent", "Scene 2 now scrubs harder. That cost $0.40."),
  ]);
  await screen.findByText("Scene 2 now scrubs harder. That cost $0.40.");

  expect(screen.queryByRole("button", { name: /^Yes/ })).toBeNull();
  expect(screen.queryByRole("group", { name: "Plan for your notes" })).toBeNull();
  expect(screen.queryByRole("group", { name: "Confirm the fix" })).toBeNull();
  expect(screen.getByRole("textbox", { name: "Message" })).toBeTruthy();
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

test("the old and new clip a failed check shows are told apart", async () => {
  // As the backend posts it: a notice carrying the old clip then the new, with no scene, so
  // the scene's card keeps its newest; the scene is the suggested note's.
  await session([
    said("user", "Yes"),
    said(
      "notice",
      "Scene 2's new clip may not have your change: he doesn't scrub. Here are the old clip and the new one.",
      {
        level: "problem",
        attachments: [
          { ...file("video", "/media/s2-v1.mp4", null), position: 1 },
          { ...file("video", "/media/s2-v2.mp4", null), position: 2 },
        ],
        suggested_note: { scene: 2, text: "he doesn't scrub" },
      },
    ),
  ]);

  // Its own class: the scene card's media class squeezes a caption under a tall clip to nothing.
  const caption = screen.getByText("Scene 2 · old");
  expect(caption.closest("figure")?.className).toBe("chat-media");
  expect(screen.getByText("Scene 2 · new")).toBeTruthy();
  expect(screen.queryByRole("group", { name: "Your ad" })).toBeNull();
});

test("a clip made again from the same picture keeps that picture as its cover", async () => {
  await session([
    said("user", "Make it"),
    said("agent", "", {
      attachments: [file("picture", "/media/s3-v1.png", 3, "v1")],
      step: made(3, "starting_picture"),
    }),
    said("agent", "", {
      attachments: [file("video", "/media/s3-v1.mp4", 3, "v1")],
      step: made(3, "clip"),
    }),
    said("agent", "", {
      attachments: [file("video", "/media/s3-v2.mp4", 3, "v2 scrubs harder")],
      step: made(3, "clip"),
    }),
    said("agent", "A new picture for scene 3.", {
      attachments: [file("picture", "/media/s3-v3.png", 3, "v3 blue sink")],
      step: made(3, "starting_picture"),
    }),
  ]);

  const posters = screen
    .getAllByLabelText("Video the agent made")
    .map((clip) => clip.getAttribute("poster"));
  expect(posters).toEqual(["/media/s3-v1.png", "/media/s3-v1.png"]);
});

test("a suggested note while another scene is still being made keeps the producer working", async () => {
  await session([
    said("user", "Yes"),
    said("notice", "Remaking scene 4's clip.", { step: started(4, "clip") }),
    said("notice", "Scene 2's new clip may not have your change.", {
      suggested_note: { scene: 2, text: "He scrubs the sole harder" },
    }),
  ]);

  expect(screen.getByText("The producer is working")).toBeTruthy();
  expect(screen.queryByText("Waiting for your answer")).toBeNull();
});

test("a video with no scene that came with a scene step isn't taken for the finished ad", async () => {
  await session([
    said("user", "Make it"),
    said("agent", "", {
      attachments: [file("video", "/media/old-clip.mp4", null)],
      step: made(1, "clip"),
    }),
  ]);
  expect(screen.queryByRole("group", { name: "Your ad" })).toBeNull();
});

test("back-to-back notices that pictures started read as one line", async () => {
  await session([
    said("user", "Make it"),
    ...[1, 2, 3, 4, 5].map((scene) =>
      said("notice", `Making scene ${scene}'s starting picture.`, { step: started(scene) }),
    ),
    said("agent", "", {
      attachments: [1, 2, 3, 4].map((scene) => ({
        ...file("picture", `/media/s${scene}.png`, scene, "v1"),
        position: scene,
      })),
      step: made(1, "starting_picture"),
    }),
    said("notice", "Remaking scene 2's starting picture.", { step: started(2) }),
    said("notice", "Remaking scene 4's starting picture.", { step: started(4) }),
    said("notice", "Making scene 1's clip.", { step: started(1, "clip") }),
    said("notice", "Remaking scene 3's starting picture.", { step: started(3) }),
    said("notice", "Remaking scene 2's starting picture.", { step: started(2) }),
    said("notice", "Remaking scene 2's starting picture.", { step: started(2) }),
  ]);

  expect(screen.getByText("Making pictures for scenes 1-5")).toBeTruthy();
  expect(screen.queryByText("Making scene 3's starting picture.")).toBeNull();
  expect(screen.getByText("Remaking pictures for scenes 2, 4")).toBeTruthy();
  expect(screen.getByText("Making scene 1's clip.")).toBeTruthy();
  expect(screen.getByText("Remaking pictures for scenes 2-3")).toBeTruthy();
});

test("once a session has started, the message box asks for a message, not a product link", async () => {
  await session([said("user", "https://shop.example/shoes"), said("agent", "On it.")]);
  expect(screen.getByRole("textbox", { name: "Message" }).getAttribute("placeholder")).toBe(
    "Write a message or answer the producer...",
  );
});

/** The scrolling thread, sized as a real browser would lay it out: `height` of messages in a
 * `view`-tall window. jsdom lays nothing out, so the sizes are set by hand. */
function sizedThread(height: number, view: number) {
  const el = screen.getByRole("list", { name: "Conversation" }).closest(".thread") as HTMLElement;
  Object.defineProperty(el, "scrollHeight", { configurable: true, value: height });
  Object.defineProperty(el, "clientHeight", { configurable: true, value: view });
  const scrollTo = vi.fn();
  el.scrollTo = scrollTo as unknown as HTMLElement["scrollTo"];
  return { el, scrollTo };
}

/** The reader scrolls the thread to `top`. */
function scrollThread(el: HTMLElement, top: number) {
  el.scrollTop = top;
  fireEvent.scroll(el);
}

const arrow = () => screen.queryByRole("button", { name: "Scroll to the newest message" });

test("the down arrow shows only while the reader is scrolled up, and goes once they're back down", async () => {
  await session([said("user", "https://shop.example/shoes"), said("agent", "On it.")]);
  const { el } = sizedThread(2000, 500);

  scrollThread(el, 1500);
  expect(arrow()).toBeNull();

  scrollThread(el, 600);
  expect(arrow()).toBeTruthy();

  scrollThread(el, 1490);
  expect(arrow()).toBeNull();
});

test("clicking the down arrow glides to the newest message and follows it again", async () => {
  await session([said("user", "https://shop.example/shoes"), said("agent", "On it.")]);
  const { el, scrollTo } = sizedThread(2000, 500);
  scrollThread(el, 1500);
  scrollThread(el, 600);

  await userEvent.click(arrow() as HTMLElement);

  expect(scrollTo).toHaveBeenCalledWith({ top: 2000, behavior: "smooth" });
  expect(arrow()).toBeNull();
  // The glide's own scroll events, still short of the bottom, don't bring the arrow back.
  scrollThread(el, 900);
  expect(arrow()).toBeNull();
});

test.each([1400, 390])(
  "a watch notice opens its exact clip in the Scenes panel at width %s",
  async (width) => {
    vi.stubGlobal("innerWidth", width);
    const text = "Please watch scene 1's clip: I couldn't check whether it's right.";
    const messages = [
      said("user", "https://shop.example/shoes"),
      said("agent", "", { attachments: [file("video", "/old.mp4", 1, "v1")] }),
      said("agent", "", { attachments: [file("video", "/new.mp4", 1, "v2")] }),
      said("agent", "Here is the ad", { attachments: [file("video", "/ad.mp4", null)] }),
      said("notice", text, { watch: { scene: 1, clip: 11 } }),
    ].map((message, index) => ({ ...message, seq: index + 1 }));
    vi.stubGlobal("fetch", async (url: string) =>
      Response.json(
        url.endsWith("/scenes/")
          ? [
              {
                scene: 1,
                versions: [
                  {
                    number: 1,
                    label: "v1",
                    clip: 11,
                    url: "/old.mp4",
                    picture: null,
                    verdict: "unsure",
                    problem: "",
                    in_ad: false,
                    free: true,
                  },
                  {
                    number: 2,
                    label: "v2",
                    clip: 12,
                    url: "/new.mp4",
                    picture: null,
                    verdict: "pass",
                    problem: "",
                    in_ad: true,
                    free: false,
                  },
                ],
              },
            ]
          : messages,
      ),
    );
    render(
      <Chat
        sessionId="session-1"
        title="Shoes"
        sidebarOpen
        onShowSidebar={() => {}}
        onSent={() => {}}
      />,
    );
    await screen.findByText("https://shop.example/shoes");
    if (width === 1400) await userEvent.click(screen.getByRole("button", { name: /Scenes/ }));
    expect(screen.queryByRole("article", { name: "Scene 1" })).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: text }));
    const card = await screen.findByRole("article", { name: "Scene 1" });
    expect(within(card).getByLabelText("Scene 1's v1").getAttribute("src")).toBe("/old.mp4#t=0.1");
    expect(
      screen.getByRole("button", { name: text }).closest(".notice-message")?.querySelector("video"),
    ).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: /Scenes/ }));
    await userEvent.click(screen.getByRole("button", { name: text }));
    expect(await screen.findByRole("article", { name: "Scene 1" })).toBeTruthy();
  },
);

test("a watch notice waits for its clip to be published instead of playing a different version", async () => {
  const target = {
    number: 1,
    label: "v1",
    clip: 11,
    url: "/old.mp4",
    picture: null,
    verdict: "unsure",
    problem: "",
    in_ad: false,
    free: true,
  };
  const current = { ...target, number: 2, clip: 12, url: "/new.mp4", verdict: "pass", in_ad: true };
  let publish: ((response: Response) => void) | undefined;
  let asked = 0;
  const messages = [
    said("user", "https://shop.example/shoes"),
    said("agent", "", { attachments: [file("video", "/new.mp4", 1, "v2")] }),
    said("notice", "Please watch the earlier clip.", { watch: { scene: 1, clip: 11 } }),
  ].map((message, index) => ({ ...message, seq: index + 1 }));
  vi.stubGlobal("fetch", async (url: string) => {
    if (!url.endsWith("/scenes/")) return Response.json(messages);
    if (++asked === 1) return Response.json([{ scene: 1, versions: [current] }]);
    return new Promise<Response>((resolve) => {
      publish = resolve;
    });
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
  await screen.findByText("https://shop.example/shoes");
  await userEvent.click(screen.getByRole("button", { name: "Please watch the earlier clip." }));
  const card = screen.getByRole("article", { name: "Scene 1" });
  expect(within(card).getByText("Loading this clip…")).toBeTruthy();
  expect(card.querySelector("video")).toBeNull();
  await waitFor(() => expect(publish).toBeDefined());
  publish!(Response.json([{ scene: 1, versions: [target, current] }]));
  await waitFor(() =>
    expect(within(card).getByLabelText("Scene 1's v1").getAttribute("src")).toBe("/old.mp4#t=0.1"),
  );
});
