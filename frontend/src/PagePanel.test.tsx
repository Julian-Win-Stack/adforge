import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { Chat } from "./Chat";
import type { Message } from "./api";

const NOW = "2026-10-19T22:00:00Z";
const CANT_READ =
  "The page at https://shop.example/p/mug couldn't be read. Paste its text and attach its photos below, or send a working link.";
const PAGE = "Glazed stoneware mug\n$24\nHolds 12 oz. Dishwasher safe. Made by hand in Portugal.";

/** One message as the server sends it; `seq` is filled in when it is posted. */
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

const cantRead = () => said("notice", CANT_READ, { level: "problem", wants_pasted_page: true });

/** Stands in for the session's messages endpoint: what's on the server, what was sent to it,
 * and the server's side of the page panel (it posts code's answer to a pasted page). */
function server(start: Omit<Message, "seq">[]) {
  const messages: Message[] = [];
  const sent: { text: string; photos: string[]; pasted_page: string | null }[] = [];
  let refusal: Record<string, string[]> | null = null;
  const post = (message: Omit<Message, "seq">) => {
    const numbered = { ...message, seq: messages.length + 1 };
    messages.push(numbered);
    return numbered;
  };
  start.forEach(post);

  vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
    if (init?.method === "POST") {
      if (refusal !== null) {
        const body = refusal;
        refusal = null;
        return Response.json(body, { status: 400 });
      }
      // A message with no photos and no panel comes as JSON, as the composer sends it.
      const form = init.body instanceof FormData ? init.body : null;
      const photos = (form?.getAll("photos") ?? []).map((photo) => (photo as File).name);
      const text = form ? String(form.get("text")) : JSON.parse(String(init.body)).text;
      const pasted = (form?.get("pasted_page") as string | null) ?? null;
      sent.push({ text, photos, pasted_page: pasted });
      const message = post(
        said("user", text, {
          pasted_page: pasted === "true",
          pasted_page_result: "",
          attachments: photos.map((name, i) => ({
            position: i + 1,
            kind: "picture",
            url: `/media/${name}`,
            scene: null,
          })),
        }),
      );
      return Response.json({ session: { id: "session-1", name: "Mug", created_at: NOW }, message });
    }
    if (url.startsWith("/media/")) {
      return new Response(url, { headers: { "Content-Type": "image/png" } });
    }
    const after = Number(new URL(url, "http://x").searchParams.get("after") ?? 0);
    return Response.json(messages.filter((message) => message.seq > after));
  });

  return {
    sent,
    post,
    /** The next message sent is refused with these reasons, as Django answers a 400. */
    refuseNextSend(reasons: Record<string, string[]>) {
      refusal = reasons;
    },
  };
}

async function openChat() {
  render(
    <Chat
      sessionId="session-1"
      title="Mug"
      sidebarOpen
      onShowSidebar={() => {}}
      onSent={() => {}}
    />,
  );
  await screen.findByText("https://shop.example/p/mug");
}

/** Lets the chat's next poll land. */
const poll = () => act(() => vi.advanceTimersByTimeAsync(2000));

const panel = () => screen.queryByRole("region", { name: "Paste your product page" });
const photo = (name: string) => new File([name], name, { type: "image/png" });

beforeEach(() => {
  // The test page can't picture a file in memory; a browser gives each photo a blob address.
  vi.spyOn(URL, "createObjectURL").mockImplementation((file) => `blob:${(file as File).name}`);
  vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => {});
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"], shouldAdvanceTime: true });
});

afterEach(() => {
  cleanup();
  localStorage.clear();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

test("the page panel opens when the page can't be read, with the chat still open under it", async () => {
  server([said("user", "https://shop.example/p/mug"), cantRead()]);
  await openChat();

  const opened = await screen.findByRole("region", { name: "Paste your product page" });
  expect(within(opened).getByLabelText("Page text")).toBeTruthy();
  expect(within(opened).getByRole("button", { name: "Add product photos" })).toBeTruthy();
  expect(screen.getByLabelText("Message")).toBeTruthy();
  expect(screen.getByRole("button", { name: "Send" })).toBeTruthy();
});

test("no page panel while the page reads fine", async () => {
  server([said("user", "https://shop.example/p/mug"), said("agent", "How long should it be?")]);
  await openChat();
  await screen.findByText("How long should it be?");
  expect(panel()).toBeNull();
});

test("Done stays off until there is page text and at least one photo", async () => {
  server([said("user", "https://shop.example/p/mug"), cantRead()]);
  const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
  await openChat();
  const opened = await screen.findByRole("region", { name: "Paste your product page" });
  const done = within(opened).getByRole("button", { name: "Done" });
  expect(done).toHaveProperty("disabled", true);

  await user.click(within(opened).getByLabelText("Page text"));
  await user.paste(PAGE);
  expect(done).toHaveProperty("disabled", true);

  await user.upload(within(opened).getByLabelText("Product photos"), [photo("front.png")]);
  expect(done).toHaveProperty("disabled", false);

  await user.clear(within(opened).getByLabelText("Page text"));
  expect(done).toHaveProperty("disabled", true);
});

test("Done sends the page text and photos as one pasted page, says it is checking, and goes away once accepted", async () => {
  const backend = server([said("user", "https://shop.example/p/mug"), cantRead()]);
  const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
  await openChat();
  const opened = await screen.findByRole("region", { name: "Paste your product page" });

  await user.click(within(opened).getByLabelText("Page text"));
  await user.paste(PAGE);
  await user.upload(within(opened).getByLabelText("Product photos"), [
    photo("front.png"),
    photo("side.png"),
  ]);
  await user.click(within(opened).getByRole("button", { name: "Done" }));

  expect(backend.sent).toEqual([
    { text: PAGE, photos: ["front.png", "side.png"], pasted_page: "true" },
  ]);
  await within(opened).findByText("Checking your page…");
  expect(within(opened).getByRole("button", { name: "Done" })).toHaveProperty("disabled", true);
  // Still checking once the chat has fetched the pasted page back, unchecked.
  await poll();
  expect(within(opened).getByText("Checking your page…")).toBeTruthy();
  expect(within(opened).getByRole("button", { name: "Done" })).toHaveProperty("disabled", true);

  backend.post(
    said("notice", "Using your text as the page and these 2 photos as the product photos.", {
      pasted_page_result: "accepted",
    }),
  );
  await poll();
  await waitFor(() => expect(panel()).toBeNull());
});

test("a paste the app can't use shows why and keeps the panel open with what was pasted", async () => {
  const backend = server([said("user", "https://shop.example/p/mug"), cantRead()]);
  const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
  await openChat();
  const opened = await screen.findByRole("region", { name: "Paste your product page" });
  await user.click(within(opened).getByLabelText("Page text"));
  await user.paste("Accept cookies");
  await user.upload(within(opened).getByLabelText("Product photos"), [photo("front.png")]);
  await user.click(within(opened).getByRole("button", { name: "Done" }));

  const reason = "It's a cookie banner, not a product page.";
  backend.post(said("notice", reason, { level: "problem", pasted_page_result: reason }));
  await poll();

  await waitFor(() => expect(within(opened).getByRole("alert").textContent).toBe(reason));
  expect(within(opened).queryByText("Checking your page…")).toBeNull();
  expect(within(opened).getByLabelText("Page text")).toHaveProperty("value", "Accept cookies");
  expect(within(opened).getByAltText("front.png")).toBeTruthy();

  // The owner fixes the paste and sends again: the old reason goes while it is checked.
  await user.clear(within(opened).getByLabelText("Page text"));
  await user.click(within(opened).getByLabelText("Page text"));
  await user.paste(PAGE);
  await user.click(within(opened).getByRole("button", { name: "Done" }));
  await within(opened).findByText("Checking your page…");
  expect(within(opened).queryByRole("alert")).toBeNull();
  expect(backend.sent.at(-1)).toEqual({ text: PAGE, photos: ["front.png"], pasted_page: "true" });
});

test("a refused send says why in the panel and keeps everything for another try", async () => {
  const backend = server([said("user", "https://shop.example/p/mug"), cantRead()]);
  const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
  await openChat();
  const opened = await screen.findByRole("region", { name: "Paste your product page" });
  await user.click(within(opened).getByLabelText("Page text"));
  await user.paste(PAGE);
  await user.upload(within(opened).getByLabelText("Product photos"), [photo("front.png")]);

  backend.refuseNextSend({ photos: ["Attach at least one photo of the product."] });
  await user.click(within(opened).getByRole("button", { name: "Done" }));

  expect(within(opened).getByRole("alert").textContent).toBe(
    "Attach at least one photo of the product.",
  );
  expect(within(opened).getByLabelText("Page text")).toHaveProperty("value", PAGE);
  expect(within(opened).getByRole("button", { name: "Done" })).toHaveProperty("disabled", false);
});

test("a reopened chat reads the result off the pasted message itself", async () => {
  const pasted = said("user", PAGE, { pasted_page: true, pasted_page_result: "" });
  server([said("user", "https://shop.example/p/mug"), cantRead(), pasted]);
  await openChat();
  const opened = await screen.findByRole("region", { name: "Paste your product page" });
  expect(within(opened).getByText("Checking your page…")).toBeTruthy();
  cleanup();

  server([
    said("user", "https://shop.example/p/mug"),
    cantRead(),
    { ...pasted, pasted_page_result: "accepted" },
    said("agent", "Here's the plan."),
  ]);
  await openChat();
  await screen.findByText("Here's the plan.");
  expect(panel()).toBeNull();
});

test("a new link read fine takes the panel away; one that can't be read brings it back", async () => {
  const backend = server([said("user", "https://shop.example/p/mug"), cantRead()]);
  const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
  await openChat();
  await screen.findByRole("region", { name: "Paste your product page" });

  await user.type(screen.getByLabelText("Message"), "try https://shop.example/mug-2");
  await user.click(screen.getByRole("button", { name: "Send" }));
  // Sent, not yet read: the panel stays until the app has the page.
  expect(panel()).not.toBeNull();

  backend.post(said("notice", "Read https://shop.example/mug-2.", { wants_pasted_page: false }));
  backend.post(said("agent", "Read it. How long should the ad be?"));
  await poll();
  await waitFor(() => expect(panel()).toBeNull());

  backend.post(said("user", "actually https://shop.example/mug-3"));
  backend.post(cantRead());
  backend.post(said("agent", "That page can't be read either."));
  await poll();
  await screen.findByRole("region", { name: "Paste your product page" });
});

test("a question typed while the panel is open doesn't take the panel away", async () => {
  const backend = server([said("user", "https://shop.example/p/mug"), cantRead()]);
  await openChat();
  await screen.findByRole("region", { name: "Paste your product page" });
  backend.post(said("user", "what should I copy?"));
  backend.post(said("agent", "Everything on the product page, from the name down."));
  await poll();
  await screen.findByText("Everything on the product page, from the name down.");
  expect(panel()).not.toBeNull();
});

test("photos come in by drop, show as thumbnails and can be taken out", async () => {
  const backend = server([said("user", "https://shop.example/p/mug"), cantRead()]);
  const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
  await openChat();
  const opened = await screen.findByRole("region", { name: "Paste your product page" });
  const zone = within(opened).getByRole("button", { name: "Add product photos" });

  fireEvent.drop(zone, {
    dataTransfer: {
      files: [photo("front.png"), new File(["x"], "notes.pdf", { type: "application/pdf" })],
    },
  });
  expect(within(opened).getByAltText("front.png")).toBeTruthy();
  expect(within(opened).queryByAltText("notes.pdf")).toBeNull();
  expect(within(opened).getByText(/only photos/i)).toBeTruthy();

  await user.upload(within(opened).getByLabelText("Product photos"), [photo("side.png")]);
  await user.click(within(opened).getByRole("button", { name: "Remove front.png" }));
  expect(within(opened).queryByAltText("front.png")).toBeNull();

  await user.click(within(opened).getByLabelText("Page text"));
  await user.paste(PAGE);
  await user.click(within(opened).getByRole("button", { name: "Done" }));
  expect(backend.sent.at(-1)?.photos).toEqual(["side.png"]);
});

test("the photo drop opens the picker from the keyboard too", async () => {
  server([said("user", "https://shop.example/p/mug"), cantRead()]);
  const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
  await openChat();
  const opened = await screen.findByRole("region", { name: "Paste your product page" });
  const picker = within(opened).getByLabelText("Product photos") as HTMLInputElement;
  const opens = vi.spyOn(picker, "click");

  within(opened).getByRole("button", { name: "Add product photos" }).focus();
  await user.keyboard("{Enter}");
  expect(opens).toHaveBeenCalledTimes(1);
});

test("the panel folds away to make room for the chat, and opens again with the paste kept", async () => {
  server([said("user", "https://shop.example/p/mug"), cantRead()]);
  const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
  await openChat();
  const opened = await screen.findByRole("region", { name: "Paste your product page" });
  await user.click(within(opened).getByLabelText("Page text"));
  await user.paste(PAGE);

  await user.click(within(opened).getByRole("button", { name: "Fold away" }));
  expect(within(opened).queryByLabelText("Page text")).toBeNull();
  await user.click(within(opened).getByRole("button", { name: /^Paste your page/ }));
  expect(within(opened).getByLabelText("Page text")).toHaveProperty("value", PAGE);
});

test("no working dots while a pasted page is checked or after it can't be used; they come once it is accepted", async () => {
  const backend = server([said("user", "https://shop.example/p/mug"), cantRead()]);
  const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
  await openChat();
  const opened = await screen.findByRole("region", { name: "Paste your product page" });
  await user.click(within(opened).getByLabelText("Page text"));
  await user.paste("Loading...");
  await user.upload(within(opened).getByLabelText("Product photos"), [photo("front.png")]);
  await user.click(within(opened).getByRole("button", { name: "Done" }));
  await within(opened).findByText("Checking your page…");
  expect(screen.queryByText("The producer is working")).toBeNull();

  backend.post(said("notice", "That's a loading screen.", { pasted_page_result: "x" }));
  await poll();
  await within(opened).findByRole("alert");
  expect(screen.queryByText("The producer is working")).toBeNull();

  await user.clear(within(opened).getByLabelText("Page text"));
  await user.paste(PAGE);
  await user.click(within(opened).getByRole("button", { name: "Done" }));
  backend.post(said("notice", "Using your text as the page.", { pasted_page_result: "accepted" }));
  await poll();
  await screen.findByText("The producer is working");
});

test("the pasted page shows in the chat as a short card, its text one tap away", async () => {
  const long = Array.from({ length: 3000 }, (_, i) => `word${i}`).join(" ");
  server([
    said("user", "https://shop.example/p/mug"),
    cantRead(),
    said("user", long, {
      pasted_page: true,
      pasted_page_result: "accepted",
      attachments: [
        { position: 1, kind: "picture", url: "/media/front.png", scene: null },
        { position: 2, kind: "picture", url: "/media/side.png", scene: null },
      ],
    }),
  ]);
  await openChat();
  const card = await screen.findByRole("group", { name: "Your product page" });
  expect(card.textContent).toContain("3,000 words");
  expect(card.textContent).toContain("2 photos");
  expect(within(card).queryByText(long)).toBeNull();
  await userEvent.click(within(card).getByRole("button", { name: "Show the text" }));
  expect(within(card).getByText(long)).toBeTruthy();
});

test("a reopened chat after a paste the app couldn't use puts that paste back in the panel", async () => {
  const backend = server([
    said("user", "https://shop.example/p/mug"),
    cantRead(),
    said("user", "Accept cookies", {
      pasted_page: true,
      pasted_page_result: "It's a cookie banner.",
      attachments: [{ position: 1, kind: "picture", url: "/media/front.png", scene: null }],
    }),
  ]);
  const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
  await openChat();
  const opened = await screen.findByRole("region", { name: "Paste your product page" });
  expect(within(opened).getByLabelText("Page text")).toHaveProperty("value", "Accept cookies");
  expect(within(opened).getByAltText("front.png").getAttribute("src")).toBe("/media/front.png");

  await user.clear(within(opened).getByLabelText("Page text"));
  await user.paste(PAGE);
  await user.click(within(opened).getByRole("button", { name: "Done" }));
  await waitFor(() =>
    expect(backend.sent.at(-1)).toEqual({ text: PAGE, photos: ["front.png"], pasted_page: "true" }),
  );
});

test("text pasted but not yet sent is still there after a reload", async () => {
  server([said("user", "https://shop.example/p/mug"), cantRead()]);
  const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
  await openChat();
  let opened = await screen.findByRole("region", { name: "Paste your product page" });
  await user.click(within(opened).getByLabelText("Page text"));
  await user.paste(PAGE);
  cleanup();

  await openChat();
  opened = await screen.findByRole("region", { name: "Paste your product page" });
  expect(within(opened).getByLabelText("Page text")).toHaveProperty("value", PAGE);
});

test("the same photo added twice is kept once", async () => {
  server([said("user", "https://shop.example/p/mug"), cantRead()]);
  const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
  await openChat();
  const opened = await screen.findByRole("region", { name: "Paste your product page" });
  await user.upload(within(opened).getByLabelText("Product photos"), [photo("front.png")]);
  await user.upload(within(opened).getByLabelText("Product photos"), [photo("front.png")]);
  expect(within(opened).getAllByAltText("front.png")).toHaveLength(1);
});

test("the hint says what is still missing, and the reason goes once the paste is changed", async () => {
  const backend = server([said("user", "https://shop.example/p/mug"), cantRead()]);
  const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
  await openChat();
  const opened = await screen.findByRole("region", { name: "Paste your product page" });
  await user.click(within(opened).getByLabelText("Page text"));
  await user.paste("Accept cookies");
  expect(within(opened).getByText("Add at least one photo.")).toBeTruthy();
  await user.upload(within(opened).getByLabelText("Product photos"), [photo("front.png")]);
  await user.click(within(opened).getByRole("button", { name: "Done" }));
  backend.post(
    said("notice", "It's a cookie banner.", { pasted_page_result: "It's a cookie banner." }),
  );
  await poll();
  await within(opened).findByRole("alert");

  await user.type(within(opened).getByLabelText("Page text"), " more");
  expect(within(opened).queryByRole("alert")).toBeNull();
});

test("a paste put back after a reload stays when another link can't be read either", async () => {
  const backend = server([
    said("user", "https://shop.example/p/mug"),
    cantRead(),
    said("user", "Accept cookies", {
      pasted_page: true,
      pasted_page_result: "It's a cookie banner.",
      attachments: [
        {
          position: 1,
          kind: "picture",
          url: "/media/0f1e2d3c-4b5a-6978-8a9b-0c1d2e3f4a5b-front.png",
          scene: null,
        },
      ],
    }),
  ]);
  const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
  await openChat();
  const opened = await screen.findByRole("region", { name: "Paste your product page" });
  expect(within(opened).getByAltText("front.png")).toBeTruthy();
  await user.upload(within(opened).getByLabelText("Product photos"), [photo("side.png")]);

  backend.post(said("user", "https://shop.example/mug-2"));
  backend.post(cantRead());
  await poll();
  await screen.findAllByText(CANT_READ);
  expect(within(panel()!).getByLabelText("Page text")).toHaveProperty("value", "Accept cookies");
  expect(within(panel()!).getAllByRole("img")).toHaveLength(2);
});

test("a reopened chat shows the paste being checked, not an empty panel", async () => {
  server([
    said("user", "https://shop.example/p/mug"),
    cantRead(),
    said("user", PAGE, { pasted_page: true, pasted_page_result: "" }),
  ]);
  await openChat();
  const opened = await screen.findByRole("region", { name: "Paste your product page" });
  expect(within(opened).getByLabelText("Page text")).toHaveProperty("value", PAGE);
  expect(within(opened).getByText("Checking your page…")).toBeTruthy();
  expect(within(opened).getByRole("button", { name: "Done" })).toHaveProperty("disabled", true);
});

test("a paste longer than the app takes says so before Done", async () => {
  server([said("user", "https://shop.example/p/mug"), cantRead()]);
  await openChat();
  const opened = await screen.findByRole("region", { name: "Paste your product page" });
  const box = within(opened).getByLabelText("Page text");
  fireEvent.change(box, { target: { value: "m".repeat(200_001) } });
  await userEvent.upload(within(opened).getByLabelText("Product photos"), [photo("front.png")]);

  expect(within(opened).getByText("200,001 of 200,000 characters")).toBeTruthy();
  expect(within(opened).getByText(/That's too long/)).toBeTruthy();
  expect(within(opened).getByRole("button", { name: "Done" })).toHaveProperty("disabled", true);
});
