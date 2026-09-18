import { act, cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { App } from "./App";
import type { ActivityEntry, JobStatus, Question, Scene } from "./api";

/** Stands in for Django's /api/jobs/ endpoints: holds one job and its activity log, and
 * answers each poll with only the entries numbered above `after`, like the real API. */
function fakeBackend() {
  const job = {
    id: "job-1",
    product_url: "https://shop.example/products/mug",
    target_seconds: 15,
    status: "queued" as JobStatus,
    created_at: "2026-09-17T10:00:00Z",
  };
  const activity: ActivityEntry[] = [];
  const requests: string[] = [];
  const answers: (string | FormData)[] = [];
  const state = { scenes: [] as Scene[], question: null as Question | null };
  // While set, polls wait on it, as a poll still in flight would.
  let heldPolls: Promise<void> | null = null;

  vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    requests.push(`${method} ${url}`);
    if (method === "POST" && url === "/api/jobs/") return Response.json(job, { status: 201 });
    const poll = url.match(/^\/api\/jobs\/job-1\/\?after=(\d+)$/);
    if (method === "GET" && poll) {
      if (heldPolls) await heldPolls;
      const after = Number(poll[1]);
      return Response.json({
        ...job,
        photos: [],
        scenes: state.scenes,
        question: state.question,
        activity: activity.filter((e) => e.seq > after),
      });
    }
    if (method === "POST" && url === "/api/jobs/job-1/answer/") {
      answers.push(init?.body instanceof FormData ? init.body : String(init?.body));
      return new Response(null, { status: 202 });
    }
    throw new Error(`Unexpected request: ${method} ${url}`);
  });

  return {
    job,
    requests,
    answers,
    state,
    /** Hold every poll until the returned function is called. */
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
    record(message: string) {
      activity.push({
        seq: activity.length + 1,
        message,
        reason: `Because of ${message}.`,
        created_at: "2026-09-17T10:00:00Z",
      });
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

test("shows each activity entry once, in order, as the job runs, then stops asking", async () => {
  const backend = fakeBackend();
  const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
  render(<App />);

  await user.type(screen.getByLabelText(/product page link/i), backend.job.product_url);
  await user.type(screen.getByLabelText(/target length/i), "15");
  backend.job.status = "reading_page";
  backend.record("Reading the product page");
  await user.click(screen.getByRole("button", { name: "Start" }));
  await screen.findByText("Reading the product page");

  backend.record("Saved 2 product photos");
  backend.record("The page is readable");
  backend.job.status = "page_read";
  await act(() => vi.advanceTimersByTimeAsync(2000));
  await screen.findByText("The page is readable");
  expect(screen.getByText("Page read")).toBeTruthy();

  backend.record("Planning the ad");
  backend.record("Planned 3 scenes");
  backend.job.status = "planned";
  await act(() => vi.advanceTimersByTimeAsync(2000));
  await screen.findByText("Planned 3 scenes");

  const entries = within(screen.getByRole("list"))
    .getAllByRole("listitem")
    .map((item) => item.firstChild?.textContent);
  expect(entries).toEqual([
    "Reading the product page",
    "Saved 2 product photos",
    "The page is readable",
    "Planning the ad",
    "Planned 3 scenes",
  ]);
  expect(screen.getByText("Ad planned")).toBeTruthy();

  const requestsWhenFinished = backend.requests.length;
  await act(() => vi.advanceTimersByTimeAsync(60_000));
  expect(backend.requests).toHaveLength(requestsWhenFinished);
});

test.each([
  { status: "needs_working_link", label: "Waiting for a working link" },
  { status: "needs_product_photos", label: "Waiting for product photos" },
  { status: "failed", label: "Failed" },
] as const)("stops asking once the job is $label", async ({ status, label }) => {
  const backend = fakeBackend();
  const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
  render(<App />);

  await user.type(screen.getByLabelText(/product page link/i), backend.job.product_url);
  backend.job.status = "reading_page";
  backend.record("Reading the product page");
  await user.click(screen.getByRole("button", { name: "Start" }));
  await screen.findByText("Reading the product page");

  backend.record("The last step");
  backend.job.status = status;
  await act(() => vi.advanceTimersByTimeAsync(2000));
  await screen.findByText("The last step");
  expect(screen.getByText(label)).toBeTruthy();

  const requestsWhenSettled = backend.requests.length;
  await act(() => vi.advanceTimersByTimeAsync(60_000));
  expect(backend.requests).toHaveLength(requestsWhenSettled);
});

test.each([
  {
    answer: () => Response.json({ detail: "Too many jobs are running." }, { status: 400 }),
    shown: "Too many jobs are running.",
    case: "an error that isn't about one field",
  },
  {
    answer: () => new Response("<h1>Bad Request</h1>", { status: 400 }),
    shown: "The server answered 400.",
    case: "an error that isn't JSON",
  },
])("says why the server refused the job for $case", async ({ answer, shown }) => {
  vi.stubGlobal("fetch", async () => answer());
  const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
  render(<App />);

  await user.type(screen.getByLabelText(/product page link/i), "https://shop.example/p/mug");
  await user.click(screen.getByRole("button", { name: "Start" }));

  expect(await screen.findByText(shown)).toBeDefined();
});

/** Starts a job through the form and waits until its first activity entry shows. */
async function startMugJob(backend: ReturnType<typeof fakeBackend>) {
  const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
  render(<App />);
  await user.type(screen.getByLabelText(/product page link/i), backend.job.product_url);
  backend.job.status = "reading_page";
  backend.record("Reading the product page");
  await user.click(screen.getByRole("button", { name: "Start" }));
  await screen.findByText("Reading the product page");
  return user;
}

test("shows each planned scene's line and slot, then stops asking", async () => {
  const backend = fakeBackend();
  await startMugJob(backend);

  backend.state.scenes = [
    { number: 1, line: "Meet the Stoneware Mug.", slot_seconds: 4, status: "planned" },
    { number: 2, line: "Yours for $24.00.", slot_seconds: 3, status: "planned" },
  ];
  backend.record("Planned 2 scenes");
  backend.job.status = "planned";
  await act(() => vi.advanceTimersByTimeAsync(2000));
  await screen.findByText("Planned 2 scenes");

  const scenes = within(screen.getByRole("table", { name: "Scenes" }))
    .getAllByRole("row")
    .slice(1) // the column headings
    .map((row) =>
      within(row)
        .getAllByRole("cell")
        .map((cell) => cell.textContent),
    );
  expect(scenes).toEqual([
    ["1", "Meet the Stoneware Mug.", "4s", "Planned"],
    ["2", "Yours for $24.00.", "3s", "Planned"],
  ]);
  expect(screen.getByText("Ad planned")).toBeTruthy();

  const requestsWhenPlanned = backend.requests.length;
  await act(() => vi.advanceTimersByTimeAsync(60_000));
  expect(backend.requests).toHaveLength(requestsWhenPlanned);
});

test("shows the producer's question, sends the typed answer, and follows the job again", async () => {
  const backend = fakeBackend();
  const user = await startMugJob(backend);

  backend.state.question = {
    id: 1,
    kind: "producer",
    question: "The page shows $24.00 and $28.00. Which price should the ad say?",
  };
  backend.record("The producer has a question for you");
  backend.job.status = "needs_answer";
  await act(() => vi.advanceTimersByTimeAsync(2000));
  await screen.findByText("The page shows $24.00 and $28.00. Which price should the ad say?");
  const requestsWhileWaiting = backend.requests.length;
  await act(() => vi.advanceTimersByTimeAsync(60_000));
  expect(backend.requests).toHaveLength(requestsWhileWaiting);

  await user.type(screen.getByLabelText("Your answer"), "$24.00, the sale price");
  backend.state.question = null;
  backend.record("You answered: $24.00, the sale price");
  backend.job.status = "planning";
  await user.click(screen.getByRole("button", { name: "Send answer" }));

  expect(backend.answers).toEqual([JSON.stringify({ answer: "$24.00, the sale price" })]);
  await screen.findByText("You answered: $24.00, the sale price");
  expect(screen.queryByLabelText("Your answer")).toBeNull();

  backend.record("Planned 3 scenes");
  backend.job.status = "planned";
  await act(() => vi.advanceTimersByTimeAsync(2000));
  await screen.findByText("Planned 3 scenes");
});

test("an answered question's form goes at once, and the same question asked again starts empty", async () => {
  const backend = fakeBackend();
  const user = await startMugJob(backend);
  const question = "Which price should the ad say?";
  backend.state.question = { id: 1, kind: "producer", question };
  backend.job.status = "needs_answer";
  await act(() => vi.advanceTimersByTimeAsync(2000));
  await user.type(await screen.findByLabelText("Your answer"), "$24.00");

  // The producer asks the same thing again, and the page only hears of it when a poll lands.
  backend.state.question = { id: 2, kind: "producer", question };
  const release = backend.holdPolls();
  await user.click(screen.getByRole("button", { name: "Send answer" }));
  expect(backend.answers).toEqual([JSON.stringify({ answer: "$24.00" })]);
  expect(screen.queryByLabelText("Your answer")).toBeNull();

  release();
  const asked = (await screen.findByLabelText("Your answer")) as HTMLTextAreaElement;
  expect(asked.value).toBe("");
});

test("asks for a working link and sends the one typed", async () => {
  const backend = fakeBackend();
  const user = await startMugJob(backend);

  backend.state.question = {
    id: 1,
    kind: "working_link",
    question: "That link didn't lead to one product's page. Can you send a link that does?",
  };
  backend.job.status = "needs_working_link";
  backend.record("The page couldn't be read");
  await act(() => vi.advanceTimersByTimeAsync(2000));
  await screen.findByText(
    "That link didn't lead to one product's page. Can you send a link that does?",
  );

  await user.type(screen.getByLabelText("Working link"), "https://shop.example/products/mug-2");
  backend.state.question = null;
  backend.job.status = "reading_page";
  backend.record("You sent a new link: https://shop.example/products/mug-2");
  await user.click(screen.getByRole("button", { name: "Send answer" }));

  expect(backend.answers).toEqual([
    JSON.stringify({ answer: "https://shop.example/products/mug-2" }),
  ]);
  await screen.findByText("You sent a new link: https://shop.example/products/mug-2");
});

test("asks for product photos and uploads the ones picked", async () => {
  const backend = fakeBackend();
  const user = await startMugJob(backend);

  backend.state.question = {
    id: 1,
    kind: "product_photos",
    question: "The page has no photo of the product. Can you upload at least one?",
  };
  backend.job.status = "needs_product_photos";
  backend.record("No usable product photos");
  await act(() => vi.advanceTimersByTimeAsync(2000));
  await screen.findByText("The page has no photo of the product. Can you upload at least one?");

  const front = new File(["front of the mug"], "mug-front.png", { type: "image/png" });
  const side = new File(["side of the mug"], "mug-side.jpg", { type: "image/jpeg" });
  await user.upload(screen.getByLabelText("Product photos"), [front, side]);
  backend.state.question = null;
  backend.job.status = "planning";
  backend.record("You uploaded 2 product photos");
  await user.click(screen.getByRole("button", { name: "Send answer" }));

  expect(backend.answers).toHaveLength(1);
  const sent = backend.answers[0] as FormData;
  expect(sent.getAll("photos").map((photo) => (photo as File).name)).toEqual([
    "mug-front.png",
    "mug-side.jpg",
  ]);
  await screen.findByText("You uploaded 2 product photos");
});

test("says why an answer was refused and keeps the question open", async () => {
  const backend = fakeBackend();
  const user = await startMugJob(backend);
  backend.state.question = {
    id: 1,
    kind: "working_link",
    question: "Can you send a working link?",
  };
  backend.job.status = "needs_working_link";
  backend.record("The page couldn't be read");
  await act(() => vi.advanceTimersByTimeAsync(2000));
  await screen.findByText("Can you send a working link?");

  const answered = globalThis.fetch;
  vi.stubGlobal("fetch", async (url: string, init?: RequestInit) =>
    url.endsWith("/answer/")
      ? Response.json({ answer: ["Enter a valid URL."] }, { status: 400 })
      : answered(url, init),
  );
  await user.type(screen.getByLabelText("Working link"), "https://shop.example/x");
  await user.click(screen.getByRole("button", { name: "Send answer" }));

  await screen.findByText("Enter a valid URL.");
  expect(screen.getByLabelText("Working link")).toBeTruthy();
});
