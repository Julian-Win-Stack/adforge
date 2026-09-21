import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { App } from "./App";
import type { Attachment, Message, Session } from "./api";

const NOW = "2026-09-20T10:00:00Z";

type StoredSession = Session & { messages: Message[] };

/** Stands in for Django's /api/sessions/ endpoints, keeping sessions and their messages
 * the way the server does: numbered in order, named from the first thing the user says. */
function fakeBackend() {
  const sessions: StoredSession[] = [];
  const requests: string[] = [];
  const sent: { text: string; photos: string[] }[] = [];
  let heldPolls: Promise<void> | null = null;
  let refusal: Record<string, string[]> | null = null;

  const summary = ({ id, name, created_at }: StoredSession): Session => ({ id, name, created_at });

  function find(id: string) {
    const session = sessions.find((one) => one.id === id);
    if (session === undefined) throw new Error(`No session ${id}`);
    return session;
  }

  function add(
    session: StoredSession,
    role: Message["role"],
    text: string,
    attachments: Attachment[] = [],
  ) {
    const message = { seq: session.messages.length + 1, role, text, created_at: NOW, attachments };
    session.messages.push(message);
    if (message.seq === 1 && session.name === "" && text) session.name = text.slice(0, 80);
    return message;
  }

  function start(name = "") {
    const session = { id: `session-${sessions.length + 1}`, name, created_at: NOW, messages: [] };
    sessions.unshift(session);
    return session;
  }

  vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    requests.push(`${method} ${url}`);

    if (url === "/api/sessions/") {
      if (method === "POST") return Response.json(summary(start()), { status: 201 });
      return Response.json(sessions.map(summary));
    }

    const messages = url.match(/^\/api\/sessions\/([\w-]+)\/messages\/(?:\?after=(\d+))?$/);
    if (messages) {
      const session = find(messages[1]);
      if (method === "POST") {
        if (refusal !== null) {
          const reasons = refusal;
          refusal = null;
          return Response.json(reasons, { status: 400 });
        }
        let text: string;
        const photos: string[] = [];
        if (init?.body instanceof FormData) {
          text = String(init.body.get("text"));
          for (const photo of init.body.getAll("photos")) photos.push((photo as File).name);
        } else {
          text = JSON.parse(String(init?.body)).text;
        }
        sent.push({ text, photos });
        const carried = photos.map((name, i) => ({
          position: i + 1,
          kind: "picture" as const,
          url: `/media/${name}`,
        }));
        const message = add(session, "user", text, carried);
        return Response.json({ session: summary(session), message }, { status: 201 });
      }
      if (heldPolls) await heldPolls;
      const after = Number(messages[2] ?? 0);
      return Response.json(session.messages.filter((message) => message.seq > after));
    }

    const renamed = url.match(/^\/api\/sessions\/([\w-]+)\/$/);
    if (renamed && method === "PATCH") {
      const session = find(renamed[1]);
      session.name = JSON.parse(String(init?.body)).name;
      return Response.json(summary(session));
    }

    throw new Error(`Unexpected request: ${method} ${url}`);
  });

  return {
    requests,
    sent,
    /** A session that already exists when the page loads. */
    existing(name: string, said: [Message["role"], string][]) {
      const session = start(name);
      for (const [role, text] of said) add(session, role, text);
      return session.id;
    },
    /** The agent says something in the newest session, as it would while the page polls. */
    agentSays(text: string, attachments: Attachment[] = []) {
      add(sessions[0], "agent", text, attachments);
    },
    /** The next message sent is refused with these reasons, as Django answers a 400. */
    refuseNextSend(reasons: Record<string, string[]>) {
      refusal = reasons;
    },
    /** Keeps every poll waiting, as if the server were busy, until the returned function runs. */
    holdPolls() {
      let release = () => {};
      heldPolls = new Promise((resolve) => {
        release = () => {
          heldPolls = null;
          resolve();
        };
      });
      return release;
    },
  };
}

beforeEach(() => {
  // The clock still moves on its own (Testing Library waits on it); tests jump it ahead.
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"], shouldAdvanceTime: true });
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

function conversation() {
  return within(screen.getByRole("list", { name: "Conversation" }))
    .queryAllByRole("listitem")
    .map((item) => item.textContent);
}

/** Waits until the conversation shows `text`. The list is looked up afresh each try,
 * since a new session's first message swaps the chat for the saved session's. */
function findSaid(text: string) {
  return waitFor(() => within(screen.getByRole("list", { name: "Conversation" })).getByText(text));
}

function sessionNames() {
  return within(screen.getByRole("navigation", { name: "Sessions" }))
    .getAllByRole("listitem")
    .map((item) => item.querySelector("button")?.textContent);
}

test("the first message starts a session, names it, and the agent's replies arrive as it polls", async () => {
  const backend = fakeBackend();
  const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
  render(<App />);

  await user.type(screen.getByLabelText("Message"), "Make me an ad for https://shop.example/p/mug");
  await user.click(screen.getByRole("button", { name: "Send" }));

  await findSaid("Make me an ad for https://shop.example/p/mug");
  expect(sessionNames()).toEqual(["Make me an ad for https://shop.example/p/mug"]);
  expect(screen.getByLabelText("Message")).toHaveProperty("value", "");

  backend.agentSays("Reading the product page");
  backend.agentSays("How long should the ad be?");
  await act(() => vi.advanceTimersByTimeAsync(2000));
  await findSaid("How long should the ad be?");

  expect(conversation()).toEqual([
    "YouMake me an ad for https://shop.example/p/mug",
    "AgentReading the product page",
    "AgentHow long should the ad be?",
  ]);
  const polls = backend.requests.filter((request) => request.includes("?after="));
  expect(polls.slice(0, 2)).toEqual([
    "GET /api/sessions/session-1/messages/?after=0",
    "GET /api/sessions/session-1/messages/?after=1",
  ]);

  // Nothing new since: another poll shows nothing twice.
  await act(() => vi.advanceTimersByTimeAsync(2000));
  expect(conversation()).toHaveLength(3);
  expect(backend.requests.at(-1)).toBe("GET /api/sessions/session-1/messages/?after=3");
});

test("shows a picture, plays a sound and plays a video the agent made", async () => {
  const backend = fakeBackend();
  const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
  render(<App />);
  await user.type(screen.getByLabelText("Message"), "Make me an ad for my mug");
  await user.click(screen.getByRole("button", { name: "Send" }));
  await findSaid("Make me an ad for my mug");

  backend.agentSays("Here's who will present it, and how they sound.", [
    { position: 1, kind: "picture", url: "/media/jobs/1/person.png" },
    { position: 2, kind: "sound", url: "/media/jobs/1/voice.mp3" },
  ]);
  backend.agentSays("Here's the ad.", [
    { position: 1, kind: "video", url: "/media/jobs/1/ad.mp4" },
  ]);
  await act(() => vi.advanceTimersByTimeAsync(2000));
  await findSaid("Here's the ad.");

  expect(screen.getByAltText("Picture the agent made (1)").getAttribute("src")).toBe(
    "/media/jobs/1/person.png",
  );
  const sound = screen.getByLabelText("Sound the agent made");
  expect(sound.tagName).toBe("AUDIO");
  expect(sound.hasAttribute("controls")).toBe(true);
  expect(sound.getAttribute("src")).toBe("/media/jobs/1/voice.mp3");
  const video = screen.getByLabelText("Video the agent made");
  expect(video.tagName).toBe("VIDEO");
  expect(video.hasAttribute("controls")).toBe(true);
  expect(video.getAttribute("src")).toBe("/media/jobs/1/ad.mp4");
});

test("attaches photos to a message", async () => {
  const backend = fakeBackend();
  const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
  render(<App />);

  await user.type(screen.getByLabelText("Message"), "This is my mug");
  await user.upload(screen.getByLabelText("Photos"), [
    new File(["front"], "front.png", { type: "image/png" }),
    new File(["side"], "side.png", { type: "image/png" }),
  ]);
  await user.click(screen.getByRole("button", { name: "Send" }));

  await screen.findByAltText("Photo you attached (2)");
  expect(backend.sent).toEqual([{ text: "This is my mug", photos: ["front.png", "side.png"] }]);
  expect(screen.getByAltText("Photo you attached (1)").getAttribute("src")).toBe(
    "/media/front.png",
  );
});

test("renames a session", async () => {
  const backend = fakeBackend();
  backend.existing("Make me an ad for my mug", [["user", "Make me an ad for my mug"]]);
  const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
  render(<App />);

  await user.click(await screen.findByRole("button", { name: "Make me an ad for my mug" }));
  await user.click(screen.getByRole("button", { name: "Rename" }));
  await user.clear(screen.getByLabelText("Session name"));
  await user.type(screen.getByLabelText("Session name"), "Kiln & Co mug");
  await user.click(screen.getByRole("button", { name: "Save" }));

  await screen.findByRole("button", { name: "Kiln & Co mug" });
  expect(backend.requests).toContain("PATCH /api/sessions/session-1/");
  expect(sessionNames()).toEqual(["Kiln & Co mug"]);
});

test("lists sessions newest first and reopens one with its whole history", async () => {
  const backend = fakeBackend();
  backend.existing("Mug ad", [
    ["user", "Make me an ad for my mug"],
    ["agent", "How long should it be?"],
    ["user", "15 seconds"],
  ]);
  backend.existing("Kettle ad", [["user", "Make me an ad for my kettle"]]);
  const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
  render(<App />);

  await screen.findByRole("button", { name: "Kettle ad" });
  expect(sessionNames()).toEqual(["Kettle ad", "Mug ad"]);
  expect(conversation()).toEqual([]);

  await user.click(screen.getByRole("button", { name: "Mug ad" }));
  await findSaid("15 seconds");
  expect(conversation()).toEqual([
    "YouMake me an ad for my mug",
    "AgentHow long should it be?",
    "You15 seconds",
  ]);

  await user.click(screen.getByRole("button", { name: "Kettle ad" }));
  await findSaid("Make me an ad for my kettle");
  expect(conversation()).toEqual(["YouMake me an ad for my kettle"]);

  // A session started now is the newest of all, so it goes to the top of the list.
  await user.click(screen.getByRole("button", { name: "New session" }));
  await user.type(screen.getByLabelText("Message"), "Make me an ad for my teapot");
  await user.click(screen.getByRole("button", { name: "Send" }));

  await findSaid("Make me an ad for my teapot");
  expect(sessionNames()).toEqual(["Make me an ad for my teapot", "Kettle ad", "Mug ad"]);
});

test("takes a message while the agent is still working", async () => {
  const backend = fakeBackend();
  backend.existing("Mug ad", [
    ["user", "Make me an ad for my mug"],
    ["agent", "Planning the ad"],
  ]);
  const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
  render(<App />);
  await user.click(await screen.findByRole("button", { name: "Mug ad" }));
  await findSaid("Planning the ad");

  // The agent is busy and the next poll hasn't come back, but the box stays open.
  const release = backend.holdPolls();
  await act(() => vi.advanceTimersByTimeAsync(2000));
  await user.type(screen.getByLabelText("Message"), "Make it 10 seconds instead");
  await user.click(screen.getByRole("button", { name: "Send" }));

  await findSaid("Make it 10 seconds instead");
  expect(backend.sent).toEqual([{ text: "Make it 10 seconds instead", photos: [] }]);

  backend.agentSays("Changing it to 10 seconds");
  release();
  await findSaid("Changing it to 10 seconds");
  expect(conversation()).toEqual([
    "YouMake me an ad for my mug",
    "AgentPlanning the ad",
    "YouMake it 10 seconds instead",
    "AgentChanging it to 10 seconds",
  ]);
});

test.each([
  {
    field: "text",
    reason: "Ensure this field has no more than 10000 characters.",
    photo: null,
  },
  {
    field: "photos",
    reason: "mug.png is over 15 MB.",
    photo: new File(["a very big mug"], "mug.png", { type: "image/png" }),
  },
])(
  "says why the server refused a message about its $field, and trying again stays in one session",
  async ({ field, reason, photo }) => {
    const backend = fakeBackend();
    backend.refuseNextSend({ [field]: [reason] });
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    render(<App />);

    await user.type(screen.getByLabelText("Message"), "This is my mug");
    if (photo) await user.upload(screen.getByLabelText("Photos"), photo);
    await user.click(screen.getByRole("button", { name: "Send" }));

    expect(await screen.findByText(reason)).toBeDefined();
    expect(backend.sent).toEqual([]);

    // What was typed and picked is still there, so sending again is one click.
    await user.click(screen.getByRole("button", { name: "Send" }));
    await findSaid("This is my mug");
    expect(backend.sent).toEqual([{ text: "This is my mug", photos: photo ? ["mug.png"] : [] }]);
    expect(backend.requests.filter((request) => request === "POST /api/sessions/")).toHaveLength(1);
    expect(sessionNames()).toEqual(["This is my mug"]);
  },
);
