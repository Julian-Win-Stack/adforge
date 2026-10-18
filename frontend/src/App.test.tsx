import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { App } from "./App";
import type { Attachment, Message, PlanNotice, SceneStep, Session } from "./api";

const NOW = "2026-09-20T10:00:00Z";

type StoredSession = Session & { messages: Message[] };

/** Stands in for Django's /api/sessions/ endpoints, keeping sessions and their messages
 * the way the server does: numbered in order, named from the first thing the user says. */
function fakeBackend() {
  const sessions: StoredSession[] = [];
  const requests: string[] = [];
  const sent: { text: string; photos: string[]; plan?: number; use_clip?: number }[] = [];
  let heldPolls: Promise<void> | null = null;
  let heldSends: Promise<void> | null = null;
  let pollsFail = false;
  let nextSendDrops = false;
  let listingFails = false;
  let refusal: { status: number; body: Record<string, unknown> } | null = null;

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
    level?: Message["level"],
    step: SceneStep | null = null,
    suggested_note: Message["suggested_note"] = null,
    plan: Message["plan"] = null,
    tries: Message["tries"] = null,
    use_clip: Message["use_clip"] = null,
  ) {
    const message = {
      seq: session.messages.length + 1,
      role,
      level: level ?? ("" as const),
      text,
      created_at: NOW,
      attachments,
      step,
      suggested_note,
      plan,
      tries,
      use_clip,
    };
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
      if (listingFails) throw new TypeError("Failed to fetch");
      return Response.json(sessions.map(summary));
    }

    const messages = url.match(/^\/api\/sessions\/([\w-]+)\/messages\/(?:\?after=(\d+))?$/);
    if (messages) {
      const session = find(messages[1]);
      if (method === "POST") {
        if (heldSends) await heldSends;
        if (nextSendDrops) {
          nextSendDrops = false;
          throw new TypeError("Failed to fetch");
        }
        if (refusal !== null) {
          const { status, body } = refusal;
          refusal = null;
          return Response.json(body, { status });
        }
        let text: string;
        let plan: number | undefined;
        let use_clip: number | undefined;
        const photos: string[] = [];
        if (init?.body instanceof FormData) {
          text = String(init.body.get("text"));
          for (const photo of init.body.getAll("photos")) photos.push((photo as File).name);
        } else {
          ({ text, plan, use_clip } = JSON.parse(String(init?.body)));
        }
        if (use_clip !== undefined) {
          sent.push({ text, photos, use_clip });
          // The server words the tap itself, as chat/api.py does.
          const said = add(session, "user", "Use it.", [], undefined, null, null, null, null);
          said.use_clip = use_clip;
          return Response.json({ session: summary(session), message: said }, { status: 201 });
        }
        sent.push(plan === undefined ? { text, photos } : { text, photos, plan });
        const carried = photos.map((name, i) => ({
          position: i + 1,
          kind: "picture" as const,
          url: `/media/${name}`,
          scene: null,
        }));
        const message = add(
          session,
          "user",
          text,
          carried,
          undefined,
          null,
          null,
          plan === undefined ? null : { id: plan, event: "yes", notice: null },
        );
        return Response.json({ session: summary(session), message }, { status: 201 });
      }
      if (heldPolls) await heldPolls;
      if (pollsFail) throw new TypeError("Failed to fetch");
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
    /** The session called `name`, to add to after another session was made. */
    sessionNamed(name: string) {
      const session = sessions.find((one) => one.name === name);
      if (session === undefined) throw new Error(`No session ${name}`);
      return session;
    },
    /** Someone says something in a given session; a notice can carry its scene's step. */
    say(
      session: StoredSession,
      role: Message["role"],
      text: string,
      step: SceneStep | null = null,
    ) {
      add(session, role, text, [], role === "notice" ? "info" : undefined, step);
    },
    /** The agent says something in the newest session, as it would while the page polls. A
     * scene's picture or clip comes with what it says of that scene's step. */
    agentSays(text: string, attachments: Attachment[] = [], step: SceneStep | null = null) {
      add(sessions[0], "agent", text, attachments, undefined, step);
    },
    /** Code posts a notice in the newest session: a step fell back or failed, or a scene's
     * picture or clip started. */
    notice(text: string, level: "problem" | "info", step: SceneStep | null = null) {
      add(sessions[0], "notice", text, [], level, step);
    },
    /** Code posts a notice in the newest session that suggests a note for a scene, as when
     * the check of a fix found the change missing. */
    suggestNote(text: string, attachments: Attachment[], scene: number, note: string) {
      add(sessions[0], "notice", text, attachments, "problem", null, { scene, text: note });
    },
    /** Code posts a fix plan in the newest session, for the owner to say yes to. */
    showPlan(notice: PlanNotice) {
      add(sessions[0], "notice", "Here's the plan for your notes.", [], "info", null, null, {
        id: notice.plan,
        event: "shown",
        notice,
      });
    },
    /** Code posts a notice in the newest session showing every try of a scene's clip the
     * judge failed every try of. */
    showTries(tries: NonNullable<Message["tries"]>) {
      add(
        sessions[0],
        "notice",
        "The clip judge found a problem in all 3 tries at scene 2's clip.",
        [],
        "info",
        null,
        null,
        null,
        tries,
      );
    },
    /** Code says in the newest session that a plan is going ahead or was dropped. */
    settlePlan(plan: number, event: "confirmed" | "declined") {
      const said = event === "confirmed" ? "Going ahead with the plan." : "Plan dropped.";
      add(sessions[0], "notice", said, [], "info", null, null, { id: plan, event, notice: null });
    },
    /** The next message sent is refused with these reasons, as Django answers a 400. */
    refuseNextSend(reasons: Record<string, string[]>) {
      refusal = { status: 400, body: reasons };
    },
    /** The next message sent is refused because another session is working, as Django
     * answers a 409. */
    refuseNextSendAsBusy(detail: string) {
      refusal = { status: 409, body: { detail } };
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
    /** Keeps every message sent waiting, as if the server were slow, until the returned
     * function runs; the message lands then. */
    holdSends() {
      let release = () => {};
      heldSends = new Promise((resolve) => {
        release = () => {
          heldSends = null;
          resolve();
        };
      });
      return release;
    },
    /** Every poll fails to reach the server, as when the network drops, until the returned
     * function runs. */
    failPolls() {
      pollsFail = true;
      return () => {
        pollsFail = false;
      };
    },
    /** The next message sent never reaches the server, as when the network drops. */
    dropNextSend() {
      nextSendDrops = true;
    },
    /** The sessions can't be listed, as when the server is down while the page loads. */
    cantListSessions() {
      listingFails = true;
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
    { position: 1, kind: "picture", url: "/media/jobs/1/person.png", scene: null },
    { position: 2, kind: "sound", url: "/media/jobs/1/voice.mp3", scene: null },
  ]);
  backend.agentSays("Here's the ad.", [
    { position: 1, kind: "video", url: "/media/jobs/1/ad.mp4", scene: null },
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

test("shows a scene's clip as it is made, after the notice that it started", async () => {
  const backend = fakeBackend();
  const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
  render(<App />);
  await user.type(screen.getByLabelText("Message"), "Make scene 1's clip");
  await user.click(screen.getByRole("button", { name: "Send" }));
  await findSaid("Make scene 1's clip");

  backend.notice("Making scene 1's clip.", "info", { scene: 1, kind: "clip", state: "started" });
  backend.agentSays("I've started scene 1's clip.");
  backend.agentSays(
    "",
    [{ position: 1, kind: "video", url: "/media/jobs/1/clip-1.mp4", scene: 1 }],
    { scene: 1, kind: "clip", state: "made" },
  );
  backend.agentSays("Scene 1's clip is ready.");
  await act(() => vi.advanceTimersByTimeAsync(2000));
  await findSaid("Scene 1's clip is ready.");

  expect(screen.getByText("Making scene 1's clip.")).toBeTruthy();
  expect(screen.getByLabelText("Video the agent made").getAttribute("src")).toBe(
    "/media/jobs/1/clip-1.mp4",
  );
});

test("keeps the working dots going when a scene's clip lands before the agent answers", async () => {
  const backend = fakeBackend();
  backend.existing("Mug ad", [["user", "Is scene 1 done yet?"]]);
  const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
  render(<App />);
  await user.click(await screen.findByRole("button", { name: "Mug ad" }));
  await findSaid("Is scene 1 done yet?");

  backend.agentSays(
    "",
    [{ position: 1, kind: "video", url: "/media/jobs/1/clip-1.mp4", scene: 1 }],
    { scene: 1, kind: "clip", state: "made" },
  );
  await act(() => vi.advanceTimersByTimeAsync(2000));
  await screen.findByLabelText("Video the agent made");

  // The clip isn't the agent's answer to what the user asked, so it is still working.
  expect(screen.getByText("The producer is working")).toBeDefined();
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

test("shows a notice as its own line, red or grey, and keeps the working dots going", async () => {
  const backend = fakeBackend();
  backend.existing("Mug ad", [["user", "Make me an ad for my mug"]]);
  const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
  render(<App />);
  await user.click(await screen.findByRole("button", { name: "Mug ad" }));
  await findSaid("Make me an ad for my mug");

  backend.notice("Firecrawl couldn't open the page. Read it with the plain download.", "problem");
  backend.notice("No official record of the product was found, which is normal.", "info");
  await act(() => vi.advanceTimersByTimeAsync(2000));
  await findSaid("No official record of the product was found, which is normal.");

  // Neither notice is labelled as the user's or the agent's words.
  expect(conversation()).toEqual([
    "YouMake me an ad for my mug",
    "Firecrawl couldn't open the page. Read it with the plain download.",
    "No official record of the product was found, which is normal.",
  ]);
  const [problem, info] = screen.getAllByRole("status");
  expect(problem.className).toBe("notice error");
  expect(info.className).toBe("notice info");
  // The producer hasn't answered yet, so it is still working.
  expect(screen.getByText("The producer is working")).toBeDefined();

  backend.agentSays("I read your mug's page.");
  await act(() => vi.advanceTimersByTimeAsync(2000));
  await findSaid("I read your mug's page.");
  expect(screen.queryByText("The producer is working")).toBeNull();
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

test("says so when another session is making an ad, and keeps what was typed", async () => {
  const backend = fakeBackend();
  const busy = "Another ad is being made right now. Please try again once it's finished.";
  backend.refuseNextSendAsBusy(busy);
  const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
  render(<App />);

  await user.type(screen.getByLabelText("Message"), "This is my mug");
  await user.click(screen.getByRole("button", { name: "Send" }));

  expect(await screen.findByText(busy)).toBeDefined();
  expect(backend.sent).toEqual([]);
  expect((screen.getByLabelText("Message") as HTMLTextAreaElement).value).toBe("This is my mug");
});

test("says so when a message can't reach the server, keeps it, and sends it on the next try", async () => {
  const backend = fakeBackend();
  backend.dropNextSend();
  const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
  render(<App />);

  await user.type(screen.getByLabelText("Message"), "This is my mug");
  await user.click(screen.getByRole("button", { name: "Send" }));

  const told = await screen.findByText("Couldn't reach the server. Try again.");
  expect(told.getAttribute("role")).toBe("alert");
  expect(backend.sent).toEqual([]);
  expect((screen.getByLabelText("Message") as HTMLTextAreaElement).value).toBe("This is my mug");

  await user.click(screen.getByRole("button", { name: "Send" }));
  await findSaid("This is my mug");
  expect(backend.sent).toEqual([{ text: "This is my mug", photos: [] }]);
  expect(screen.queryByText("Couldn't reach the server. Try again.")).toBeNull();
  expect(backend.requests.filter((request) => request === "POST /api/sessions/")).toHaveLength(1);
});

test("the send button is off while a message is on its way, so a second tap sends nothing", async () => {
  const backend = fakeBackend();
  backend.existing("Mug ad", [["user", "Make me an ad for my mug"]]);
  const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
  render(<App />);
  await user.click(await screen.findByRole("button", { name: "Mug ad" }));
  await findSaid("Make me an ad for my mug");
  await user.type(screen.getByLabelText("Message"), "Faster");

  const release = backend.holdSends();
  await user.click(screen.getByRole("button", { name: "Send" }));
  expect(screen.getByRole("button", { name: "Send" }).hasAttribute("disabled")).toBe(true);
  await user.click(screen.getByRole("button", { name: "Send" }));
  await user.type(screen.getByLabelText("Message"), "{Enter}");
  expect(backend.requests.filter((request) => request.startsWith("POST"))).toHaveLength(1);

  release();
  await findSaid("Faster");
  await waitFor(() =>
    expect((screen.getByLabelText("Message") as HTMLTextAreaElement).value).toBe(""),
  );
  expect(backend.sent).toEqual([{ text: "Faster", photos: [] }]);
  // Sent, the box takes the next message.
  await user.type(screen.getByLabelText("Message"), "Slower");
  expect(screen.getByRole("button", { name: "Send" }).hasAttribute("disabled")).toBe(false);
});

test("says when polling loses the server, and stops saying so once it answers again", async () => {
  const backend = fakeBackend();
  backend.existing("Mug ad", [["user", "Make me an ad for my mug"]]);
  const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
  render(<App />);
  await user.click(await screen.findByRole("button", { name: "Mug ad" }));
  await findSaid("Make me an ad for my mug");

  const recover = backend.failPolls();
  await act(() => vi.advanceTimersByTimeAsync(2000));
  const told = await screen.findByText("Lost contact with the server. Still trying...");
  expect(told.getAttribute("role")).toBe("status");
  expect(conversation()).toEqual(["YouMake me an ad for my mug"]);

  // Still trying: the next poll gets through, with what was said meanwhile.
  backend.agentSays("I read your mug's page.");
  recover();
  await act(() => vi.advanceTimersByTimeAsync(2000));
  await findSaid("I read your mug's page.");
  expect(screen.queryByText("Lost contact with the server. Still trying...")).toBeNull();
});

test("a poll still on its way when another session is opened doesn't land in it", async () => {
  const backend = fakeBackend();
  backend.existing("Mug ad", [["user", "Make me an ad for my mug"]]);
  backend.existing("Kettle ad", [["user", "Make me an ad for my kettle"]]);
  const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
  render(<App />);
  await user.click(await screen.findByRole("button", { name: "Mug ad" }));
  await findSaid("Make me an ad for my mug");

  // The mug session's poll is held; the agent speaks there meanwhile.
  const release = backend.holdPolls();
  await act(() => vi.advanceTimersByTimeAsync(2000));
  backend.say(backend.sessionNamed("Mug ad"), "agent", "Planning the mug ad");
  await user.click(screen.getByRole("button", { name: "Kettle ad" }));
  release();

  await findSaid("Make me an ad for my kettle");
  expect(conversation()).toEqual(["YouMake me an ad for my kettle"]);
  backend.say(backend.sessionNamed("Kettle ad"), "agent", "Planning the kettle ad");
  await act(() => vi.advanceTimersByTimeAsync(2000));
  await findSaid("Planning the kettle ad");
  expect(conversation()).toEqual(["YouMake me an ad for my kettle", "AgentPlanning the kettle ad"]);
  expect(screen.queryByText("Planning the mug ad")).toBeNull();
});

test("shift and enter start a new line without sending; enter alone sends", async () => {
  const backend = fakeBackend();
  const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
  render(<App />);

  await user.type(
    screen.getByLabelText("Message"),
    "Make me an ad{Shift>}{Enter}{/Shift}for my mug",
  );
  expect((screen.getByLabelText("Message") as HTMLTextAreaElement).value).toBe(
    "Make me an ad\nfor my mug",
  );
  expect(backend.sent).toEqual([]);

  await user.type(screen.getByLabelText("Message"), "{Enter}");
  await findSaid("Make me an ad for my mug");
  expect(backend.sent).toEqual([{ text: "Make me an ad\nfor my mug", photos: [] }]);
});

test("a photo taken off its chip isn't sent", async () => {
  const backend = fakeBackend();
  const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
  render(<App />);

  await user.type(screen.getByLabelText("Message"), "This is my mug");
  await user.upload(screen.getByLabelText("Photos"), [
    new File(["front"], "front.png", { type: "image/png" }),
    new File(["side"], "side.png", { type: "image/png" }),
  ]);
  await user.click(screen.getByRole("button", { name: "Remove side.png" }));
  expect(screen.queryByText("side.png")).toBeNull();
  expect(screen.getByText("front.png")).toBeDefined();
  await user.click(screen.getByRole("button", { name: "Send" }));

  await screen.findByAltText("Photo you attached (1)");
  expect(backend.sent).toEqual([{ text: "This is my mug", photos: ["front.png"] }]);
  expect(screen.queryByAltText("Photo you attached (2)")).toBeNull();
});

test("a suggestion fills the box, and a second one goes after it", async () => {
  fakeBackend();
  const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
  render(<App />);

  await user.click(
    screen.getByRole("button", { name: "Make a 15-second TikTok ad for a product page" }),
  );
  expect((screen.getByLabelText("Message") as HTMLTextAreaElement).value).toBe(
    "Make a 15-second TikTok ad for a product page",
  );
  await user.click(screen.getByRole("button", { name: "End with “Shop now”" }));
  expect((screen.getByLabelText("Message") as HTMLTextAreaElement).value).toBe(
    "Make a 15-second TikTok ad for a product page End with “Shop now”",
  );
});

function chatTitle() {
  return document.querySelector(".chat-title")?.textContent;
}

test("the chat is titled with the open session's name, Untitled without one, or New session before there is one", async () => {
  const backend = fakeBackend();
  backend.existing("Mug ad", [["user", "Make me an ad for my mug"]]);
  backend.existing("", []);
  const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
  render(<App />);
  expect(chatTitle()).toBe("New session");

  await user.click(await screen.findByRole("button", { name: "Mug ad" }));
  expect(chatTitle()).toBe("Mug ad");
  await user.click(screen.getByRole("button", { name: "Untitled" }));
  expect(chatTitle()).toBe("Untitled");
  await user.click(screen.getByRole("button", { name: "New session" }));
  expect(chatTitle()).toBe("New session");
});

test("a session named after a product link is titled with the product in the chat too", async () => {
  const backend = fakeBackend();
  backend.existing("https://www.allbirds.com/products/mens-tree-runners", [
    ["user", "https://www.allbirds.com/products/mens-tree-runners"],
  ]);
  const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
  render(<App />);
  await user.click(await screen.findByRole("button", { name: "Mens Tree Runners · allbirds.com" }));
  expect(chatTitle()).toBe("Mens Tree Runners · allbirds.com");
});

test("says so when the sessions can't be listed", async () => {
  const backend = fakeBackend();
  backend.cantListSessions();
  render(<App />);

  const told = await screen.findByRole("alert");
  expect(told.textContent).toBe("Couldn't load your sessions.");
  expect(
    within(screen.getByRole("navigation", { name: "Sessions" })).queryAllByRole("listitem"),
  ).toEqual([]);
});

/** The scene cards, as the owner sees them: each card's name and the clip or picture it
 * shows, or "nothing" while neither has been made. */
function sceneCardsShown() {
  const panel = screen.queryByRole("region", { name: "Scenes" });
  if (panel === null) return null;
  return within(panel)
    .queryAllByRole("article")
    .map((card) => {
      const media = card.querySelector("video, img");
      return [card.getAttribute("aria-label"), media?.getAttribute("src") ?? "nothing"];
    });
}

function sceneCard(scene: number) {
  return within(screen.getByRole("region", { name: "Scenes" })).getByRole("article", {
    name: `Scene ${scene}`,
  });
}

function scene(n: number, kind: Attachment["kind"], url: string): Attachment[] {
  return [{ position: 1, kind, url, scene: n }];
}

/** Opens the session called `name`, as the owner would from the sidebar. */
async function openSession(name: string) {
  const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
  render(<App />);
  await user.click(await screen.findByRole("button", { name }));
  return user;
}

async function nextPoll() {
  await act(() => vi.advanceTimersByTimeAsync(2000));
}

test("clips for scenes 1 to 3 make three cards in scene order with those clips", async () => {
  const backend = fakeBackend();
  backend.existing("Mug ad", [["user", "Make me an ad for my mug"]]);
  await openSession("Mug ad");
  await findSaid("Make me an ad for my mug");

  backend.agentSays("", scene(3, "video", "/media/c3.mp4"), {
    scene: 3,
    kind: "clip",
    state: "made",
  });
  backend.agentSays("", scene(1, "video", "/media/c1.mp4"), {
    scene: 1,
    kind: "clip",
    state: "made",
  });
  backend.agentSays("", scene(2, "video", "/media/c2.mp4"), {
    scene: 2,
    kind: "clip",
    state: "made",
  });
  await nextPoll();

  await waitFor(() =>
    expect(sceneCardsShown()).toEqual([
      ["Scene 1", "/media/c1.mp4"],
      ["Scene 2", "/media/c2.mp4"],
      ["Scene 3", "/media/c3.mp4"],
    ]),
  );
  const played = within(sceneCard(2)).getByLabelText("Scene 2's clip");
  expect(played.tagName).toBe("VIDEO");
  expect(played.hasAttribute("controls")).toBe(true);
});

test("a later clip for a scene replaces the card's clip, and the old one stays one tap away", async () => {
  const backend = fakeBackend();
  backend.existing("Mug ad", [["user", "Make me an ad for my mug"]]);
  const user = await openSession("Mug ad");
  backend.agentSays("", scene(2, "video", "/media/c2-a.mp4"), {
    scene: 2,
    kind: "clip",
    state: "made",
  });
  await nextPoll();
  await waitFor(() => expect(sceneCardsShown()).toEqual([["Scene 2", "/media/c2-a.mp4"]]));

  // While it is remade, the old clip stays on the card, marked.
  backend.notice("Remaking scene 2's clip.", "info", { scene: 2, kind: "clip", state: "started" });
  await nextPoll();
  await within(sceneCard(2)).findByText("Being remade");
  expect(sceneCardsShown()).toEqual([["Scene 2", "/media/c2-a.mp4"]]);

  backend.agentSays("", scene(2, "video", "/media/c2-b.mp4"), {
    scene: 2,
    kind: "clip",
    state: "made",
  });
  await nextPoll();
  await waitFor(() => expect(sceneCardsShown()).toEqual([["Scene 2", "/media/c2-b.mp4"]]));
  expect(within(sceneCard(2)).queryByText("Being remade")).toBeNull();

  await user.click(within(sceneCard(2)).getByRole("button", { name: "Previous" }));
  expect(within(sceneCard(2)).getByLabelText("Scene 2's previous clip").getAttribute("src")).toBe(
    "/media/c2-a.mp4",
  );
  expect(within(sceneCard(2)).getByText("Previous clip")).toBeDefined();
  await user.click(within(sceneCard(2)).getByRole("button", { name: "Latest" }));
  expect(sceneCardsShown()).toEqual([["Scene 2", "/media/c2-b.mp4"]]);

  // Looking at the previous clip when a newer one lands goes back to the newest.
  await user.click(within(sceneCard(2)).getByRole("button", { name: "Previous" }));
  backend.agentSays("", scene(2, "video", "/media/c2-c.mp4"), {
    scene: 2,
    kind: "clip",
    state: "made",
  });
  await nextPoll();
  await waitFor(() => expect(sceneCardsShown()).toEqual([["Scene 2", "/media/c2-c.mp4"]]));
  expect(within(sceneCard(2)).queryByText("Previous clip")).toBeNull();
});

test("a scene's card names the version it shows, the one gone back to too", async () => {
  const backend = fakeBackend();
  backend.existing("Mug ad", [["user", "Make me an ad for my mug"]]);
  const user = await openSession("Mug ad");
  const versioned = (url: string, version: string): Attachment[] => [
    { position: 1, kind: "video", url, scene: 2, version },
  ];
  backend.agentSays("", versioned("/media/c2-a.mp4", "v1 first version"), {
    scene: 2,
    kind: "clip",
    state: "made",
  });
  backend.agentSays("", versioned("/media/c2-b.mp4", "v2 black toilet"), {
    scene: 2,
    kind: "clip",
    state: "made",
  });
  await nextPoll();
  await within(sceneCard(2)).findByText("v2 black toilet");

  await user.click(within(sceneCard(2)).getByRole("button", { name: "Previous" }));
  expect(within(sceneCard(2)).getByText("v1 first version")).toBeDefined();
  await user.click(within(sceneCard(2)).getByRole("button", { name: "Latest" }));

  // Going back shows the earlier clip again, as a version of its own.
  backend.agentSays("", versioned("/media/c2-a.mp4", "v3 back to v1"), null);
  await nextPoll();
  await within(sceneCard(2)).findByText("v3 back to v1");
  expect(sceneCardsShown()).toEqual([["Scene 2", "/media/c2-a.mp4"]]);

  // Going back after the line changed brings back only the picture: it is the newest
  // version, shown beside the clip it replaces until its own clip is made.
  backend.agentSays(
    "",
    [{ position: 1, kind: "picture", url: "/media/p2-b.png", scene: 2, version: "v4 back to v2" }],
    null,
  );
  await nextPoll();
  await within(sceneCard(2)).findByText("v4 back to v2");
  expect(sceneCardsShown()).toEqual([["Scene 2", "/media/c2-a.mp4"]]);
  expect(within(sceneCard(2)).getByAltText("Scene 2's new starting picture")).toBeDefined();
  await user.click(within(sceneCard(2)).getByRole("button", { name: "Previous" }));
  expect(within(sceneCard(2)).getByText("v2 black toilet")).toBeDefined();
});

test("a scene's card with only a picture names the picture's version", async () => {
  const backend = fakeBackend();
  backend.existing("Mug ad", [["user", "Make me an ad for my mug"]]);
  await openSession("Mug ad");
  backend.agentSays(
    "",
    [{ position: 1, kind: "picture", url: "/media/p1.png", scene: 1, version: "v1 first version" }],
    { scene: 1, kind: "starting_picture", state: "made" },
  );
  await nextPoll();
  await within(sceneCard(1)).findByText("v1 first version");
});

test("a scene with a picture and no clip yet shows the picture, and its clip once made", async () => {
  const backend = fakeBackend();
  backend.existing("Mug ad", [["user", "Make me an ad for my mug"]]);
  await openSession("Mug ad");
  backend.agentSays("", scene(1, "picture", "/media/p1.png"), {
    scene: 1,
    kind: "starting_picture",
    state: "made",
  });
  await nextPoll();
  await waitFor(() => expect(sceneCardsShown()).toEqual([["Scene 1", "/media/p1.png"]]));
  expect(within(sceneCard(1)).getByAltText("Scene 1's starting picture")).toBeDefined();

  backend.agentSays("", scene(1, "video", "/media/c1.mp4"), {
    scene: 1,
    kind: "clip",
    state: "made",
  });
  await nextPoll();
  await waitFor(() => expect(sceneCardsShown()).toEqual([["Scene 1", "/media/c1.mp4"]]));
  // Until the clip loads, its first frame is the picture it was made from.
  expect(within(sceneCard(1)).getByLabelText("Scene 1's clip").getAttribute("poster")).toBe(
    "/media/p1.png",
  );
  // A newer picture doesn't take the clip's place, nor become its first frame.
  backend.agentSays("", scene(1, "picture", "/media/p1-b.png"), {
    scene: 1,
    kind: "starting_picture",
    state: "made",
  });
  await nextPoll();
  await waitFor(() =>
    expect(
      screen.getAllByAltText("Picture the agent made (1)").map((img) => img.getAttribute("src")),
    ).toContain("/media/p1-b.png"),
  );
  expect(sceneCardsShown()).toEqual([["Scene 1", "/media/c1.mp4"]]);
  expect(within(sceneCard(1)).getByLabelText("Scene 1's clip").getAttribute("poster")).toBe(
    "/media/p1.png",
  ); // The clip is now out of date: the card says so and shows the new picture beside it.
  expect(within(sceneCard(1)).getByText("Waiting to finish")).toBeDefined();
  expect(
    within(sceneCard(1)).getByAltText("Scene 1's new starting picture").getAttribute("src"),
  ).toBe("/media/p1-b.png");

  backend.agentSays("", scene(1, "video", "/media/c1-b.mp4"), {
    scene: 1,
    kind: "clip",
    state: "made",
  });
  await nextPoll();
  await waitFor(() => expect(sceneCardsShown()).toEqual([["Scene 1", "/media/c1-b.mp4"]]));
  expect(within(sceneCard(1)).queryByText("Waiting to finish")).toBeNull();
  expect(within(sceneCard(1)).queryByAltText("Scene 1's new starting picture")).toBeNull();
});

test("a card says while its picture or clip is being made, until it is made or fails", async () => {
  const backend = fakeBackend();
  backend.existing("Mug ad", [["user", "Make me an ad for my mug"]]);
  await openSession("Mug ad");

  backend.notice("Making scene 1's starting picture.", "info", {
    scene: 1,
    kind: "starting_picture",
    state: "started",
  });
  backend.notice("Making scene 2's clip.", "info", { scene: 2, kind: "clip", state: "started" });
  await nextPoll();
  await within(await findCard(1)).findByText("Making the picture");
  expect(within(sceneCard(2)).getByText("Making the clip")).toBeDefined();
  expect(sceneCardsShown()).toEqual([
    ["Scene 1", "nothing"],
    ["Scene 2", "nothing"],
  ]);
  expect(screen.getByRole("button", { name: /Scenes/ }).textContent).toContain("2 being made");

  backend.agentSays("", scene(1, "picture", "/media/p1.png"), {
    scene: 1,
    kind: "starting_picture",
    state: "made",
  });
  backend.notice("Scene 2's clip couldn't be made.", "problem", {
    scene: 2,
    kind: "clip",
    state: "not_made",
  });
  await nextPoll();
  await within(sceneCard(2)).findByText("Couldn't make the clip");
  expect(within(sceneCard(1)).queryByText("Making the picture")).toBeNull();
  expect(within(sceneCard(2)).queryByText("Making the clip")).toBeNull();
  expect(screen.getByRole("button", { name: /Scenes/ }).textContent).not.toContain("being made");
});

function findCard(scene: number) {
  return waitFor(() => sceneCard(scene));
}

test("the finished ad, the portrait and the voice make no card, and no scenes means no panel", async () => {
  const backend = fakeBackend();
  backend.existing("Mug ad", [["user", "Make me an ad for my mug"]]);
  await openSession("Mug ad");
  backend.agentSays("Here's who will present it.", [
    { position: 1, kind: "picture", url: "/media/person.png", scene: null },
    { position: 2, kind: "sound", url: "/media/voice.mp3", scene: null },
  ]);
  backend.agentSays("Here's the ad.", [
    { position: 1, kind: "video", url: "/media/ad.mp4", scene: null },
  ]);
  await nextPoll();
  await findSaid("Here's the ad.");
  expect(sceneCardsShown()).toBeNull();
});

test("reopening a session rebuilds its cards from the whole history", async () => {
  const backend = fakeBackend();
  backend.existing("Mug ad", [["user", "Make me an ad for my mug"]]);
  backend.agentSays("", scene(1, "video", "/media/c1-a.mp4"), {
    scene: 1,
    kind: "clip",
    state: "made",
  });
  backend.agentSays("", scene(2, "picture", "/media/p2.png"), {
    scene: 2,
    kind: "starting_picture",
    state: "made",
  });
  backend.agentSays("", scene(1, "video", "/media/c1-b.mp4"), {
    scene: 1,
    kind: "clip",
    state: "made",
  });
  backend.existing("Kettle ad", [["user", "Make me an ad for my kettle"]]);

  const user = await openSession("Mug ad");
  await waitFor(() =>
    expect(sceneCardsShown()).toEqual([
      ["Scene 1", "/media/c1-b.mp4"],
      ["Scene 2", "/media/p2.png"],
    ]),
  );

  await user.click(screen.getByRole("button", { name: "Kettle ad" }));
  await findSaid("Make me an ad for my kettle");
  expect(sceneCardsShown()).toBeNull();

  await user.click(screen.getByRole("button", { name: "Mug ad" }));
  await waitFor(() =>
    expect(sceneCardsShown()).toEqual([
      ["Scene 1", "/media/c1-b.mp4"],
      ["Scene 2", "/media/p2.png"],
    ]),
  );
});

test("the scene cards fold away and open again", async () => {
  const backend = fakeBackend();
  backend.existing("Mug ad", [["user", "Make me an ad for my mug"]]);
  const user = await openSession("Mug ad");
  backend.agentSays("", scene(1, "video", "/media/c1.mp4"), {
    scene: 1,
    kind: "clip",
    state: "made",
  });
  await nextPoll();
  const toggle = await screen.findByRole("button", { name: /Scenes/ });
  expect(toggle.getAttribute("aria-expanded")).toBe("true");

  await user.click(toggle);
  expect(sceneCardsShown()).toEqual([]);
  expect(toggle.getAttribute("aria-expanded")).toBe("false");
  await user.click(toggle);
  expect(sceneCardsShown()).toEqual([["Scene 1", "/media/c1.mp4"]]);
});

/** An ad whose scenes 1 to 4 each have a clip, opened as the owner would. */
async function openFinishedAd() {
  const backend = fakeBackend();
  backend.existing("Mug ad", [["user", "Make me an ad for my mug"]]);
  for (const n of [1, 2, 3, 4]) {
    backend.agentSays("", scene(n, "video", `/media/c${n}.mp4`), {
      scene: n,
      kind: "clip",
      state: "made",
    });
  }
  backend.agentSays("Here's the ad.", [
    { position: 1, kind: "video", url: "/media/ad.mp4", scene: null },
  ]);
  const user = await openSession("Mug ad");
  await findSaid("Here's the ad.");
  return { backend, user };
}

function noteBox(n: number) {
  return within(sceneCard(n)).getByRole("textbox", { name: `Note for scene ${n}` });
}

function sendNotesButton() {
  return within(screen.getByRole("region", { name: "Scenes" })).getByRole("button", {
    name: /^Send( \d+)? notes?$/,
  });
}

test("notes on two scenes go as one message, one line per scene in scene order", async () => {
  const { backend, user } = await openFinishedAd();

  await user.type(noteBox(4), "Show the handle");
  await user.type(noteBox(2), "The hand just hovers, it should scrub the tile");
  await user.click(sendNotesButton());

  // The thread shows it like any message the owner typed.
  await findSaid(
    "Scene 2: The hand just hovers, it should scrub the tile Scene 4: Show the handle",
  );
  expect(backend.sent.map((one) => one.text)).toEqual([
    "Scene 2: The hand just hovers, it should scrub the tile\nScene 4: Show the handle",
  ]);
  expect((noteBox(2) as HTMLTextAreaElement).value).toBe("");
  expect((noteBox(4) as HTMLTextAreaElement).value).toBe("");
});

test("with no note anywhere, nothing can be sent and the form says what to do", async () => {
  const { backend, user } = await openFinishedAd();

  expect(sendNotesButton().hasAttribute("disabled")).toBe(true);
  expect(
    within(screen.getByRole("region", { name: "Scenes" })).getByText(
      "Write a note on any scene, then send them together.",
    ),
  ).toBeDefined();

  // Spaces alone aren't a note.
  await user.type(noteBox(3), "   ");
  expect(sendNotesButton().hasAttribute("disabled")).toBe(true);
  await user.click(sendNotesButton());
  expect(backend.sent).toEqual([]);

  await user.type(noteBox(3), "Brighter");
  expect(sendNotesButton().hasAttribute("disabled")).toBe(false);
  expect(
    within(screen.getByRole("region", { name: "Scenes" })).queryByText(
      "Write a note on any scene, then send them together.",
    ),
  ).toBeNull();
  expect(sendNotesButton().textContent).toBe("Send 1 note");
  await user.type(noteBox(1), "Slower");
  expect(sendNotesButton().textContent).toBe("Send 2 notes");
});

test("a note of several lines goes whole, its line breaks kept inside its scene's entry", async () => {
  const { backend, user } = await openFinishedAd();

  await user.type(
    noteBox(3),
    "The cloth should move.{Enter}{Enter}And the tile should shine after.  ",
  );
  await user.type(noteBox(1), "  Slower");
  await user.click(sendNotesButton());

  await waitFor(() =>
    expect(backend.sent.map((one) => one.text)).toEqual([
      "Scene 1: Slower\nScene 3: The cloth should move.\n\nAnd the tile should shine after.",
    ]),
  );
});

function yesButton() {
  return screen.queryByRole("button", { name: "Yes" });
}

test("the yes button comes with the producer's answer to the notes and sends a plain yes", async () => {
  const { backend, user } = await openFinishedAd();
  // The agent has spoken, but nobody has sent notes: nothing to say yes to.
  expect(yesButton()).toBeNull();

  await user.type(noteBox(2), "Scrub the tile");
  await user.click(sendNotesButton());
  await findSaid("Scene 2: Scrub the tile");
  expect(yesButton()).toBeNull();

  backend.agentSays("I'll make scene 2's hand scrub the tile. About $0.40. Shall I?");
  await nextPoll();
  await findSaid("I'll make scene 2's hand scrub the tile. About $0.40. Shall I?");
  await user.click(yesButton()!);

  await findSaid("Yes");
  expect(backend.sent.map((one) => one.text)).toEqual(["Scene 2: Scrub the tile", "Yes"]);
  expect(yesButton()).toBeNull();
});

test("another scene's step starting doesn't take away the yes for the noted scene", async () => {
  const { backend, user } = await openFinishedAd();
  await user.type(noteBox(2), "Scrub the tile");
  await user.click(sendNotesButton());
  backend.agentSays("I'll make scene 2's hand scrub the tile. Shall I?");
  backend.notice("Remaking scene 3's clip.", "info", { scene: 3, kind: "clip", state: "started" });
  await nextPoll();
  await findSaid("I'll make scene 2's hand scrub the tile. Shall I?");

  await waitFor(() => expect(yesButton()).not.toBeNull());
  await user.click(yesButton()!);
  await findSaid("Yes");
  expect(backend.sent.map((one) => one.text)).toEqual(["Scene 2: Scrub the tile", "Yes"]);
});

test("a correction typed in the chat gets a new answer, and the yes button comes back for it", async () => {
  const { backend, user } = await openFinishedAd();
  await user.type(noteBox(2), "Scrub the tile");
  await user.click(sendNotesButton());
  backend.agentSays("I'll make the hand wipe the counter. Shall I?");
  await nextPoll();
  await waitFor(() => expect(yesButton()).not.toBeNull());

  await user.type(screen.getByRole("textbox", { name: "Message" }), "No, the tile{Enter}");
  await findSaid("No, the tile");
  expect(yesButton()).toBeNull();

  backend.agentSays("I'll make the hand scrub the tile. Shall I?");
  await nextPoll();
  await waitFor(() => expect(yesButton()).not.toBeNull());

  // Once a redo has started, the producer's later words aren't a question to say yes to.
  await user.type(screen.getByRole("textbox", { name: "Message" }), "yes please{Enter}");
  backend.notice("Remaking scene 2's clip.", "info", { scene: 2, kind: "clip", state: "started" });
  backend.agentSays("On it.");
  await nextPoll();
  await findSaid("On it.");
  expect(yesButton()).toBeNull();
});

/** The owner sends notes on scenes 2 and 4 and says yes to the producer's restatement. */
async function noteAndSayYes() {
  const ad = await openFinishedAd();
  await ad.user.type(noteBox(2), "Scrub the tile");
  await ad.user.type(noteBox(4), "Show the handle");
  await ad.user.click(sendNotesButton());
  ad.backend.agentSays("Scene 2 scrubs the tile, scene 4 shows the handle. About $1. Shall I?");
  await nextPoll();
  await waitFor(() => expect(yesButton()).not.toBeNull());
  await ad.user.click(yesButton()!);
  await findSaid("Yes");
  return ad;
}

function statusOf(n: number) {
  return sceneCard(n).querySelector(".scene-status")?.textContent ?? null;
}

test("after the yes, a scene being redone keeps its old clip, marked, until the new clip lands", async () => {
  const { backend } = await noteAndSayYes();

  // Scene 2's clip is redone; scene 4's picture first, then its clip.
  backend.notice("Remaking scene 2's clip.", "info", { scene: 2, kind: "clip", state: "started" });
  backend.notice("Making scene 4's starting picture.", "info", {
    scene: 4,
    kind: "starting_picture",
    state: "started",
  });
  await nextPoll();
  await waitFor(() => expect(statusOf(2)).toBe("Being remade"));
  expect(statusOf(4)).toBe("Being remade");
  expect([statusOf(1), statusOf(3)]).toEqual([null, null]);
  expect(sceneCardsShown()).toEqual([
    ["Scene 1", "/media/c1.mp4"],
    ["Scene 2", "/media/c2.mp4"],
    ["Scene 3", "/media/c3.mp4"],
    ["Scene 4", "/media/c4.mp4"],
  ]);

  // Scene 4's new picture lands: its clip is still to be remade.
  backend.agentSays("", scene(4, "picture", "/media/p4-b.png"), {
    scene: 4,
    kind: "starting_picture",
    state: "made",
  });
  await nextPoll();
  await waitFor(() =>
    expect(within(sceneCard(4)).queryByAltText("Scene 4's new starting picture")).not.toBeNull(),
  );
  expect(statusOf(4)).toBe("Being remade");
  expect(screen.getByRole("button", { name: /Scenes/ }).textContent).toContain("2 being made");
  backend.notice("Remaking scene 4's clip.", "info", { scene: 4, kind: "clip", state: "started" });
  backend.agentSays("", scene(2, "video", "/media/c2-b.mp4"), {
    scene: 2,
    kind: "clip",
    state: "made",
  });
  await nextPoll();
  await waitFor(() => expect(statusOf(2)).toBeNull());
  expect(statusOf(4)).toBe("Being remade");
  expect(sceneCardsShown()).toEqual([
    ["Scene 1", "/media/c1.mp4"],
    ["Scene 2", "/media/c2-b.mp4"],
    ["Scene 3", "/media/c3.mp4"],
    ["Scene 4", "/media/c4.mp4"],
  ]);

  backend.agentSays("", scene(4, "video", "/media/c4-b.mp4"), {
    scene: 4,
    kind: "clip",
    state: "made",
  });
  await nextPoll();
  await waitFor(() => expect(statusOf(4)).toBeNull());
  expect(sceneCardsShown()).toEqual([
    ["Scene 1", "/media/c1.mp4"],
    ["Scene 2", "/media/c2-b.mp4"],
    ["Scene 3", "/media/c3.mp4"],
    ["Scene 4", "/media/c4-b.mp4"],
  ]);
});

test("a redo that fails clears 'being remade' and says so, with the old clip kept", async () => {
  const { backend } = await noteAndSayYes();
  backend.notice("Remaking scene 2's clip.", "info", { scene: 2, kind: "clip", state: "started" });
  backend.notice("Making scene 4's starting picture.", "info", {
    scene: 4,
    kind: "starting_picture",
    state: "started",
  });
  await nextPoll();
  await waitFor(() => expect(statusOf(2)).toBe("Being remade"));

  // Scene 2's clip fails; scene 4's new picture lands, then its clip fails.
  backend.notice("Scene 2's clip couldn't be made.", "problem", {
    scene: 2,
    kind: "clip",
    state: "not_made",
  });
  backend.agentSays("", scene(4, "picture", "/media/p4-b.png"), {
    scene: 4,
    kind: "starting_picture",
    state: "made",
  });
  backend.notice("Remaking scene 4's clip.", "info", { scene: 4, kind: "clip", state: "started" });
  backend.notice("Scene 4's clip couldn't be made.", "problem", {
    scene: 4,
    kind: "clip",
    state: "not_made",
  });
  await nextPoll();
  await waitFor(() => expect(statusOf(2)).toBe("Couldn't make the clip"));
  expect(statusOf(4)).toBe("Couldn't make the clip");
  expect(sceneCardsShown()).toEqual([
    ["Scene 1", "/media/c1.mp4"],
    ["Scene 2", "/media/c2.mp4"],
    ["Scene 3", "/media/c3.mp4"],
    ["Scene 4", "/media/c4.mp4"],
  ]);
  expect(screen.getByRole("button", { name: /Scenes/ }).textContent).not.toContain("being made");
});

test("reopening mid-way rebuilds the beat: the yes on offer, then the scenes being remade", async () => {
  const backend = fakeBackend();
  backend.existing("Mug ad", [["user", "Make me an ad for my mug"]]);
  for (const n of [1, 2]) {
    backend.agentSays("", scene(n, "video", `/media/c${n}.mp4`), {
      scene: n,
      kind: "clip",
      state: "made",
    });
  }
  backend.existing("Kettle ad", [["user", "Make me an ad for my kettle"]]);
  const mug = backend.sessionNamed("Mug ad");
  backend.say(mug, "user", "Scene 2: Scrub the tile");
  backend.say(mug, "agent", "I'll make the hand scrub the tile. Shall I?");

  const user = await openSession("Mug ad");
  await waitFor(() => expect(yesButton()).not.toBeNull());

  await user.click(yesButton()!);
  await findSaid("Yes");
  backend.say(mug, "notice", "Remaking scene 2's clip.", {
    scene: 2,
    kind: "clip",
    state: "started",
  });
  await user.click(screen.getByRole("button", { name: "Kettle ad" }));
  await findSaid("Make me an ad for my kettle");
  await user.click(screen.getByRole("button", { name: "Mug ad" }));

  await waitFor(() => expect(statusOf(2)).toBe("Being remade"));
  expect(statusOf(1)).toBeNull();
  expect(yesButton()).toBeNull();
});

test("a clip that lands while the owner writes on its scene keeps the note and the cursor", async () => {
  const { backend, user } = await openFinishedAd();
  await user.type(noteBox(2), "Scrub");

  backend.agentSays("", scene(2, "video", "/media/c2-b.mp4"), {
    scene: 2,
    kind: "clip",
    state: "made",
  });
  await nextPoll();
  await waitFor(() => expect(sceneCardsShown()).toContainEqual(["Scene 2", "/media/c2-b.mp4"]));
  expect(document.activeElement).toBe(noteBox(2));
  await user.keyboard(" the tile");

  expect((noteBox(2) as HTMLTextAreaElement).value).toBe("Scrub the tile");
});

test("a new picture whose clip isn't being made says it's waiting, without claiming work", async () => {
  const { backend } = await noteAndSayYes();
  backend.notice("Remaking scene 2's starting picture.", "info", {
    scene: 2,
    kind: "starting_picture",
    state: "started",
  });
  backend.agentSays("", scene(2, "picture", "/media/p2-b.png"), {
    scene: 2,
    kind: "starting_picture",
    state: "made",
  });
  // The run stops before scene 2's clip starts: nothing is running any more.
  backend.notice("Scene 3's picture kept failing its check.", "problem");
  await nextPoll();
  await waitFor(() => expect(statusOf(2)).toBe("Waiting to finish"));
  expect(sceneCard(2).classList.contains("busy")).toBe(false);
  expect(screen.getByRole("button", { name: /Scenes/ }).textContent).not.toContain("being made");

  backend.notice("Remaking scene 2's clip.", "info", { scene: 2, kind: "clip", state: "started" });
  await nextPoll();
  await waitFor(() => expect(statusOf(2)).toBe("Being remade"));
});

test("a failed fix check fills its scene's note box with the reason, ready to send or change", async () => {
  const { backend, user } = await openFinishedAd();
  const reason = "The mug's handle still faces away from the camera.";

  backend.suggestNote(
    `Scene 2's new starting picture may not have your change: ${reason}`,
    [
      { position: 1, kind: "picture", url: "/media/old.png", scene: null },
      { position: 2, kind: "picture", url: "/media/new.png", scene: null },
    ],
    2,
    reason,
  );

  await nextPoll();
  await waitFor(() => expect((noteBox(2) as HTMLTextAreaElement).value).toBe(reason));
  // The notice shows the old picture and the new one.
  const notice = screen.getByText(/may not have your change/).closest("li") as HTMLElement;
  expect(
    within(notice)
      .getAllByRole("img")
      .map((shown) => shown.getAttribute("src")),
  ).toEqual(["/media/old.png", "/media/new.png"]);
  expect((noteBox(1) as HTMLTextAreaElement).value).toBe("");
  expect(sendNotesButton().textContent).toBe("Send 1 note");

  await user.type(noteBox(2), " Turn it round.");
  await user.click(sendNotesButton());
  await waitFor(() =>
    expect(backend.sent.map((one) => one.text)).toEqual([`Scene 2: ${reason} Turn it round.`]),
  );
  // Answered, the suggestion fills the box no more.
  await findSaid(`Scene 2: ${reason} Turn it round.`);
  await nextPoll();
  expect((noteBox(2) as HTMLTextAreaElement).value).toBe("");
});

test("the owner can clear a suggested note and send nothing for that scene", async () => {
  const { backend, user } = await openFinishedAd();
  backend.suggestNote(
    "Scene 3's new clip may not have your change: still hovers",
    [],
    3,
    "still hovers",
  );
  await nextPoll();
  await waitFor(() => expect((noteBox(3) as HTMLTextAreaElement).value).toBe("still hovers"));

  await user.clear(noteBox(3));

  expect((noteBox(3) as HTMLTextAreaElement).value).toBe("");
  expect(sendNotesButton().hasAttribute("disabled")).toBe(true);
});

test("a newer failed check fills a box the owner cleared for an earlier one", async () => {
  const { backend, user } = await openFinishedAd();
  backend.suggestNote("Scene 2's new starting picture may not have your change: a", [], 2, "a");
  await nextPoll();
  await waitFor(() => expect((noteBox(2) as HTMLTextAreaElement).value).toBe("a"));
  await user.clear(noteBox(2));

  // The owner answers in the chat, not the cards; the next fix fails its check too.
  await user.type(screen.getByRole("textbox", { name: "Message" }), "Go ahead{Enter}");
  await findSaid("Go ahead");
  backend.suggestNote("Scene 2's new starting picture may not have your change: b", [], 2, "b");
  await nextPoll();

  await waitFor(() => expect((noteBox(2) as HTMLTextAreaElement).value).toBe("b"));
});

type Backend = ReturnType<typeof fakeBackend>;

function scenesRegion() {
  return within(screen.getByRole("region", { name: "Scenes" }));
}

test.each([
  {
    why: "about what was written",
    reason: "Ensure this field has no more than 10000 characters.",
    refuse: (backend: Backend) =>
      backend.refuseNextSend({ text: ["Ensure this field has no more than 10000 characters."] }),
  },
  {
    why: "because another ad is being made",
    reason: "Another ad is being made right now. Please try again once it's finished.",
    refuse: (backend: Backend) =>
      backend.refuseNextSendAsBusy(
        "Another ad is being made right now. Please try again once it's finished.",
      ),
  },
])(
  "notes the server refused $why stay in their boxes, with the reason, to send again",
  async ({ reason, refuse }) => {
    const { backend, user } = await openFinishedAd();
    refuse(backend);

    await user.type(noteBox(2), "Scrub the tile");
    await user.click(sendNotesButton());

    const told = await scenesRegion().findByText(reason);
    expect(told.getAttribute("role")).toBe("alert");
    expect(backend.sent).toEqual([]);
    expect((noteBox(2) as HTMLTextAreaElement).value).toBe("Scrub the tile");
    expect(sendNotesButton().hasAttribute("disabled")).toBe(false);

    await user.click(sendNotesButton());
    await findSaid("Scene 2: Scrub the tile");
    expect(backend.sent.map((one) => one.text)).toEqual(["Scene 2: Scrub the tile"]);
    expect(scenesRegion().queryByText(reason)).toBeNull();
    expect((noteBox(2) as HTMLTextAreaElement).value).toBe("");
  },
);

/** The owner sends a note on scene 2 and the producer says back what it understood. */
async function noteAndHearBack() {
  const ad = await openFinishedAd();
  await ad.user.type(noteBox(2), "Scrub the tile");
  await ad.user.click(sendNotesButton());
  ad.backend.agentSays("I'll make scene 2's hand scrub the tile. Shall I?");
  await nextPoll();
  await waitFor(() => expect(yesButton()).not.toBeNull());
  return ad;
}

function yesBar() {
  return within(screen.getByRole("group", { name: "Confirm the fix" }));
}

test.each([
  {
    why: "another ad is being made",
    reason: "Another ad is being made right now. Please try again once it's finished.",
    refuse: (backend: Backend) =>
      backend.refuseNextSendAsBusy(
        "Another ad is being made right now. Please try again once it's finished.",
      ),
  },
  {
    why: "the server can't be reached",
    reason: "Couldn't reach the server. Try again.",
    refuse: (backend: Backend) => backend.dropNextSend(),
  },
])(
  "a yes that doesn't go through because $why says so and stays on offer",
  async ({ reason, refuse }) => {
    const { backend, user } = await noteAndHearBack();
    refuse(backend);

    await user.click(yesButton()!);

    const told = await yesBar().findByRole("alert");
    expect(told.textContent).toBe(reason);
    expect(yesBar().getByRole("button", { name: "Yes" }).hasAttribute("disabled")).toBe(false);
    expect(backend.sent.map((one) => one.text)).toEqual(["Scene 2: Scrub the tile"]);

    await user.click(yesButton()!);
    await findSaid("Yes");
    expect(backend.sent.map((one) => one.text)).toEqual(["Scene 2: Scrub the tile", "Yes"]);
  },
);

function postsTo(backend: Backend, session: string) {
  return backend.requests.filter(
    (request) => request === `POST /api/sessions/${session}/messages/`,
  );
}

test("the send-notes button is off while the notes are on their way, so a second tap sends nothing", async () => {
  const { backend, user } = await openFinishedAd();
  await user.type(noteBox(2), "Scrub the tile");

  const release = backend.holdSends();
  await user.click(sendNotesButton());
  expect(sendNotesButton().hasAttribute("disabled")).toBe(true);
  await user.click(sendNotesButton());
  expect(postsTo(backend, "session-1")).toHaveLength(1);

  release();
  await findSaid("Scene 2: Scrub the tile");
  await waitFor(() => expect((noteBox(2) as HTMLTextAreaElement).value).toBe(""));
  expect(backend.sent.map((one) => one.text)).toEqual(["Scene 2: Scrub the tile"]);
  // Sent, the form takes the next notes.
  await user.type(noteBox(4), "Show the handle");
  expect(sendNotesButton().hasAttribute("disabled")).toBe(false);
});

test("the yes button is off while the yes is on its way, so a second tap sends nothing", async () => {
  const { backend, user } = await noteAndHearBack();

  const release = backend.holdSends();
  await user.click(yesButton()!);
  expect(yesButton()!.hasAttribute("disabled")).toBe(true);
  await user.click(yesButton()!);
  expect(postsTo(backend, "session-1")).toHaveLength(2);

  release();
  await findSaid("Yes");
  expect(backend.sent.map((one) => one.text)).toEqual(["Scene 2: Scrub the tile", "Yes"]);
  expect(yesButton()).toBeNull();
});

test("folding the cards away keeps the notes being written", async () => {
  const { user } = await openFinishedAd();
  await user.type(noteBox(2), "Scrub the tile");
  const toggle = screen.getByRole("button", { name: /Scenes/ });

  await user.click(toggle);
  expect(sceneCardsShown()).toEqual([]);
  await user.click(toggle);

  expect((noteBox(2) as HTMLTextAreaElement).value).toBe("Scrub the tile");
  expect(sendNotesButton().textContent).toBe("Send 1 note");
});

test("a scene whose new picture failed its check waits for the owner while another scene is still being made", async () => {
  const { backend } = await noteAndSayYes();
  backend.notice("Remaking scene 2's starting picture.", "info", {
    scene: 2,
    kind: "starting_picture",
    state: "started",
  });
  backend.agentSays("", scene(2, "picture", "/media/p2-b.png"), {
    scene: 2,
    kind: "starting_picture",
    state: "made",
  });
  backend.notice("Remaking scene 4's clip.", "info", { scene: 4, kind: "clip", state: "started" });
  backend.suggestNote(
    "Scene 2's new starting picture may not have your change: still hovers",
    [],
    2,
    "still hovers",
  );
  await nextPoll();

  await waitFor(() => expect(statusOf(2)).toBe("Waiting to finish"));
  expect(sceneCard(2).classList.contains("busy")).toBe(false);
  expect(statusOf(4)).toBe("Being remade");
  expect(sceneCard(4).classList.contains("busy")).toBe(true);
  expect(screen.getByRole("button", { name: /Scenes/ }).textContent).toContain("1 being made");
  expect((noteBox(2) as HTMLTextAreaElement).value).toBe("still hovers");
});

test("a scene with only a picture says its picture is being remade when it starts again", async () => {
  const { backend } = await openFinishedAd();
  backend.agentSays("", scene(5, "picture", "/media/p5.png"), {
    scene: 5,
    kind: "starting_picture",
    state: "made",
  });
  await nextPoll();
  await waitFor(() => expect(sceneCardsShown()).toContainEqual(["Scene 5", "/media/p5.png"]));

  backend.notice("Remaking scene 5's starting picture.", "info", {
    scene: 5,
    kind: "starting_picture",
    state: "started",
  });
  await nextPoll();
  await waitFor(() => expect(statusOf(5)).toBe("Remaking the picture"));
  expect(sceneCard(5).classList.contains("busy")).toBe(true);
  expect(sceneCardsShown()).toContainEqual(["Scene 5", "/media/p5.png"]);
});

test("a failed clip check shows both clips in its notice, and the card keeps the scene's newest clip", async () => {
  const { backend } = await openFinishedAd();
  backend.agentSays("", scene(3, "video", "/media/c3-b.mp4"), {
    scene: 3,
    kind: "clip",
    state: "made",
  });
  backend.suggestNote(
    "Scene 3's new clip may not have your change: the hand still hovers",
    [
      { position: 1, kind: "video", url: "/media/old.mp4", scene: null },
      { position: 2, kind: "video", url: "/media/new.mp4", scene: null },
    ],
    3,
    "the hand still hovers",
  );
  await nextPoll();

  const notice = (await screen.findByText(/may not have your change/)).closest("li") as HTMLElement;
  expect(
    within(notice)
      .getAllByLabelText("Video the agent made")
      .map((shown) => shown.getAttribute("src")),
  ).toEqual(["/media/old.mp4", "/media/new.mp4"]);
  expect(sceneCardsShown()).toContainEqual(["Scene 3", "/media/c3-b.mp4"]);
  // The clips in the notice don't become the scene's: its previous clip is still c3.
  expect(within(sceneCard(3)).getByRole("button", { name: "Previous" })).toBeDefined();
  expect((noteBox(3) as HTMLTextAreaElement).value).toBe("the hand still hovers");
});

/** A plan as code posts it: scene 2 gets a new picture with the owner's photo, scene 3
 * goes back to its first version, scene 4's ask can't be done. */
function aPlan(plan = 7, overrides: Partial<PlanNotice> = {}): PlanNotice {
  const row = {
    line: "",
    overlay: "",
    go_back_to: null,
    cant: "",
    estimate_usd: "0.000000",
    estimate_up_to_usd: "0.000000",
    photos: [],
  };
  return {
    plan,
    state: "proposed",
    question: "",
    estimate_usd: "0.550000",
    estimate_up_to_usd: "1.100000",
    cants: [],
    scenes: [
      {
        ...row,
        scene: 2,
        action: "new_picture",
        change: "The toilet is black.",
        estimate_usd: "0.550000",
        estimate_up_to_usd: "1.100000",
        photos: [{ photo: 1, thing: "black toilet", in_picture: true, during_action: false }],
      },
      { ...row, scene: 3, action: "go_back", change: "Back to the first one.", go_back_to: 1 },
      {
        ...row,
        scene: 4,
        action: "no_change",
        change: "",
        cant: "The person's look can't be changed.",
      },
    ],
    ...overrides,
  };
}

function planNotices() {
  return screen.queryAllByRole("group", { name: "Plan for your notes" });
}

function planYes() {
  return screen.queryByRole("button", { name: "Yes, go ahead" });
}

async function showAPlan(plan: PlanNotice = aPlan()) {
  const ad = await openFinishedAd();
  ad.backend.showPlan(plan);
  await nextPoll();
  await waitFor(() => expect(planNotices()).toHaveLength(1));
  return ad;
}

test("a plan notice shows per scene what changes, what it costs and what can't be done", async () => {
  await showAPlan(
    aPlan(7, {
      question: "Which toilet, the one in photo 1?",
      cants: [{ kind: "music", text: "The music can't be changed." }],
    }),
  );

  const plan = within(planNotices()[0]);
  const scenes = plan.getAllByRole("listitem").map((item) => item.textContent);
  expect(scenes).toEqual([
    expect.stringContaining("Scene 2"),
    expect.stringContaining("Scene 3"),
    expect.stringContaining("Scene 4"),
  ]);
  expect(scenes[0]).toContain("New starting picture");
  expect(scenes[0]).toContain("The toilet is black.");
  expect(scenes[0]).toContain("Photo 1: black toilet");
  expect(scenes[0]).toContain("About $0.55, up to $1.10");
  expect(scenes[1]).toContain("Go back to version 1");
  expect(scenes[1]).toContain("Free");
  expect(scenes[2]).toContain("Can't: The person's look can't be changed.");
  expect(scenes[2]).not.toContain("$");
  expect(plan.getByText("Which toilet, the one in photo 1?")).toBeDefined();
  expect(plan.getByText("Can't: The music can't be changed.")).toBeDefined();
  expect(plan.getByText("About $0.55 in all, up to $1.10")).toBeDefined();
  // The plan's own yes, not the producer's.
  expect(planYes()).not.toBeNull();
  expect(yesButton()).toBeNull();
});

test("a new line is quoted in the plan", async () => {
  const row = aPlan().scenes[0];
  await showAPlan(
    aPlan(7, {
      scenes: [
        { ...row, action: "new_line", change: "Say it shorter.", line: "One wipe.", photos: [] },
      ],
    }),
  );
  const [scene] = within(planNotices()[0]).getAllByRole("listitem");
  expect(scene.textContent).toContain("New line");
  expect(scene.textContent).toContain("“One wipe.”");
});

test("a new line's new overlay is shown in the plan as what goes on screen", async () => {
  const row = aPlan().scenes[0];
  await showAPlan(
    aPlan(7, {
      scenes: [
        {
          ...row,
          action: "new_line",
          change: "Say the new price.",
          line: "Yours for $8.99.",
          overlay: "$8.99",
          photos: [],
        },
      ],
    }),
  );
  const [scene] = within(planNotices()[0]).getAllByRole("listitem");
  expect(scene.textContent).toContain("On screen: “$8.99”");
});

test("once code posts a plan, the producer's answer to the notes brings no yes of its own", async () => {
  const { backend, user } = await openFinishedAd();
  await user.type(noteBox(2), "Make the toilet black");
  await user.click(sendNotesButton());
  backend.agentSays("I'll make scene 2's toilet black. About $0.55. Shall I?");
  backend.showPlan(aPlan());
  await nextPoll();
  await waitFor(() => expect(planNotices()).toHaveLength(1));

  // Only the plan's yes is offered: the producer's restatement is no longer what is said yes to.
  expect(planYes()).not.toBeNull();
  expect(yesButton()).toBeNull();
});

test("the plan's yes button sends a yes marked with the plan, and goes away", async () => {
  const { backend, user } = await showAPlan();

  await user.click(planYes()!);

  await findSaid("Yes");
  expect(backend.sent).toEqual([{ text: "Yes", photos: [], plan: 7 }]);
  expect(planYes()).toBeNull();
  // The plan stays on screen to read back.
  expect(planNotices()).toHaveLength(1);
});

test("a typed answer to a plan goes as plain text, and the yes stays on offer until the plan is settled", async () => {
  const { backend, user } = await showAPlan();

  await user.type(screen.getByRole("textbox", { name: "Message" }), "what does it cost?{Enter}");
  await findSaid("what does it cost?");
  expect(backend.sent).toEqual([{ text: "what does it cost?", photos: [] }]);
  expect(planYes()).not.toBeNull();

  backend.settlePlan(7, "confirmed");
  await nextPoll();
  await findSaid("Going ahead with the plan.");
  expect(planYes()).toBeNull();
});

test("a declined plan takes its yes away", async () => {
  const { backend } = await showAPlan();
  backend.settlePlan(7, "declined");
  await nextPoll();
  await findSaid("Plan dropped.");
  expect(planYes()).toBeNull();
});

test("a newer plan replaces the yes: only the newest plan can be said yes to", async () => {
  const { backend, user } = await showAPlan();
  backend.showPlan(aPlan(8));
  await nextPoll();
  await waitFor(() => expect(planNotices()).toHaveLength(2));

  const [older, newer] = planNotices();
  expect(within(older).queryByRole("button", { name: "Yes, go ahead" })).toBeNull();
  await user.click(within(newer).getByRole("button", { name: "Yes, go ahead" }));
  await findSaid("Yes");
  expect(backend.sent).toEqual([{ text: "Yes", photos: [], plan: 8 }]);
});

test("a plan with nothing to do, only can'ts, offers no yes", async () => {
  await showAPlan(
    aPlan(7, {
      scenes: [],
      cants: [{ kind: "voice", text: "The voice can't be changed." }],
      estimate_usd: "0.000000",
      estimate_up_to_usd: "0.000000",
    }),
  );
  expect(planYes()).toBeNull();
  expect(screen.queryByText(/in all/)).toBeNull();
  const plan = within(planNotices()[0]);
  expect(plan.getByText("We can't do that yet")).toBeDefined();
  expect(plan.queryByText(/Here's the plan/)).toBeNull();
});

test("a plan of a can't and a scene left as it is offers no yes and prices nothing", async () => {
  const cant = aPlan().scenes[2];
  await showAPlan(
    aPlan(7, {
      scenes: [cant, { ...cant, scene: 5, cant: "", change: "It already looks right." }],
      estimate_usd: "0.000000",
      estimate_up_to_usd: "0.000000",
    }),
  );
  expect(planYes()).toBeNull();
  const plan = within(planNotices()[0]);
  expect(plan.getByText("We can't do that yet")).toBeDefined();
  // Nothing runs, so nothing is priced, not even as free.
  expect(plan.queryByText("Free")).toBeNull();
});

test("a plan that only asks shows its question and no title", async () => {
  await showAPlan(
    aPlan(7, {
      scenes: [],
      question: "Which toilet, the one in photo 1?",
      estimate_usd: "0.000000",
      estimate_up_to_usd: "0.000000",
    }),
  );
  expect(planYes()).toBeNull();
  const plan = within(planNotices()[0]);
  expect(plan.getByText("Which toilet, the one in photo 1?")).toBeDefined();
  expect(plan.queryByText(/Here's the plan|can't do that/)).toBeNull();
});

test("a reopened chat reads the plan as it stands: a settled one offers no yes", async () => {
  const backend = fakeBackend();
  backend.existing("Mug ad", [["user", "Make me an ad for my mug"]]);
  backend.showPlan(aPlan(7, { state: "confirmed" }));
  await openSession("Mug ad");
  await waitFor(() => expect(planNotices()).toHaveLength(1));
  expect(planYes()).toBeNull();
});

test("the plan's yes is off while it is on its way, so a second tap sends nothing", async () => {
  const { backend, user } = await showAPlan();

  const release = backend.holdSends();
  await user.click(planYes()!);
  expect(planYes()!.hasAttribute("disabled")).toBe(true);
  await user.click(planYes()!);
  expect(postsTo(backend, "session-1")).toHaveLength(1);

  release();
  await findSaid("Yes");
  expect(planYes()).toBeNull();
});

test("a refused yes says why and the button can be tapped again", async () => {
  const { backend, user } = await showAPlan();
  backend.refuseNextSend({ plan: ["That plan isn't waiting for an answer any more."] });

  await user.click(planYes()!);

  const told = await within(planNotices()[0]).findByRole("alert");
  expect(told.textContent).toBe("That plan isn't waiting for an answer any more.");
  expect(planYes()!.hasAttribute("disabled")).toBe(false);
});

// --- Every try of a clip the judge failed every try of (#180) ------------------------------

function threeTries(inAd: number | null = 11): NonNullable<Message["tries"]> {
  return {
    scene: 2,
    in_ad: inAd,
    clips: [
      { clip: 11, name: "First try", version: "v1", url: "/media/t1.mp4", problem: "It floats." },
      { clip: 12, name: "Remake 1", version: "v2", url: "/media/t2.mp4", problem: "It spins." },
      { clip: 13, name: "Remake 2", version: "v3", url: "/media/t3.mp4", problem: "It flips." },
    ],
  };
}

function triesShown() {
  return screen.getByRole("group", { name: "Every try at scene 2's clip" });
}

function aTry(name: string) {
  return within(triesShown()).getByRole("listitem", { name });
}

async function showTheTries(tries = threeTries()) {
  const ad = await openFinishedAd();
  ad.backend.showTries(tries);
  await nextPoll();
  await waitFor(() => triesShown());
  return ad;
}

test("every try is shown with what the judge saw, the one in the ad marked, the others swappable", async () => {
  await showTheTries();

  const tries = within(triesShown()).getAllByRole("listitem");
  expect(tries.map((one) => one.getAttribute("aria-label"))).toEqual([
    "First try",
    "Remake 1",
    "Remake 2",
  ]);
  expect(within(tries[0]).getByLabelText("Scene 2, First try").getAttribute("src")).toBe(
    "/media/t1.mp4",
  );
  expect(tries[0].textContent).toContain("The judge saw: It floats.");
  expect(tries[0].textContent).toContain("In the ad");
  expect(within(tries[0]).queryByRole("button", { name: "Use this one" })).toBeNull();
  for (const other of tries.slice(1)) {
    expect(other.textContent).not.toContain("In the ad");
    expect(within(other).getByRole("button", { name: "Use this one" })).toBeDefined();
  }
});

test("a tap on a try sends it, and it is being swapped in until the scene's clip is it", async () => {
  const { backend, user } = await showTheTries();

  await user.click(within(aTry("Remake 1")).getByRole("button", { name: "Use this one" }));

  await waitFor(() => expect(aTry("Remake 1").textContent).toContain("Swapping in…"));
  expect(backend.sent).toEqual([{ text: "", photos: [], use_clip: 12 }]);
  expect(aTry("First try").textContent).toContain("In the ad");
  const others = within(triesShown()).getAllByRole("button", { name: /Use this one|Swapping/ });
  expect(others.every((button) => button.hasAttribute("disabled"))).toBe(true);

  backend.agentSays("", scene(2, "video", "/media/t2.mp4"));
  await nextPoll();

  await waitFor(() => expect(aTry("Remake 1").textContent).toContain("In the ad"));
  expect(aTry("First try").textContent).not.toContain("In the ad");
  const back = within(aTry("First try")).getByRole("button", { name: "Use this one" });
  expect(back.hasAttribute("disabled")).toBe(false);
});

test("a tap the app couldn't act on, said in red, leaves the try in the ad as it was", async () => {
  const { backend, user } = await showTheTries();
  await user.click(within(aTry("Remake 1")).getByRole("button", { name: "Use this one" }));
  await waitFor(() => expect(aTry("Remake 1").textContent).toContain("Swapping in…"));

  backend.notice("Scene 2 couldn't use remake 1. Those tries aren't on offer any more.", "problem");
  await nextPoll();

  await waitFor(() => expect(aTry("Remake 1").textContent).not.toContain("Swapping in…"));
  expect(aTry("First try").textContent).toContain("In the ad");
  const again = within(aTry("Remake 1")).getByRole("button", { name: "Use this one" });
  expect(again.hasAttribute("disabled")).toBe(false);
});

test("once a newer clip of the scene is shown, no try can be swapped in", async () => {
  const { backend } = await showTheTries();

  backend.agentSays("", scene(2, "video", "/media/newer.mp4"), {
    scene: 2,
    kind: "clip",
    state: "made",
  });
  await nextPoll();

  await waitFor(() =>
    expect(within(triesShown()).queryAllByRole("button", { name: "Use this one" })).toEqual([]),
  );
  expect(triesShown().textContent).toContain("A newer clip of this scene is in the ad now.");
  expect(triesShown().textContent).not.toContain("In the ad");
});

test("a refused tap says why and the tries can be tapped again", async () => {
  const { backend, user } = await showTheTries();
  backend.refuseNextSend({ use_clip: ["Those tries aren't on offer any more."] });

  await user.click(within(aTry("Remake 2")).getByRole("button", { name: "Use this one" }));

  const told = await within(triesShown()).findByRole("alert");
  expect(told.textContent).toBe("Those tries aren't on offer any more.");
  const button = within(aTry("Remake 2")).getByRole("button", { name: "Use this one" });
  expect(button.hasAttribute("disabled")).toBe(false);
});

test("the tries are off while a tap is on its way, so a second tap sends nothing", async () => {
  const { backend, user } = await showTheTries();

  const release = backend.holdSends();
  await user.click(within(aTry("Remake 1")).getByRole("button", { name: "Use this one" }));
  const other = within(aTry("Remake 2")).getByRole("button", { name: "Use this one" });
  expect(other.hasAttribute("disabled")).toBe(true);
  await user.click(other);
  expect(postsTo(backend, "session-1")).toHaveLength(1);

  release();
  await waitFor(() => expect(aTry("Remake 1").textContent).toContain("Swapping in…"));
});
