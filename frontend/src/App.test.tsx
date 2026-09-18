import { act, cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { App } from "./App";
import type { ActivityEntry, JobStatus } from "./api";

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

  vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    requests.push(`${method} ${url}`);
    if (method === "POST" && url === "/api/jobs/") return Response.json(job, { status: 201 });
    const poll = url.match(/^\/api\/jobs\/job-1\/\?after=(\d+)$/);
    if (method === "GET" && poll) {
      const after = Number(poll[1]);
      return Response.json({ ...job, photos: [], activity: activity.filter((e) => e.seq > after) });
    }
    throw new Error(`Unexpected request: ${method} ${url}`);
  });

  return {
    job,
    requests,
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

  const entries = within(screen.getByRole("list"))
    .getAllByRole("listitem")
    .map((item) => item.firstChild?.textContent);
  expect(entries).toEqual([
    "Reading the product page",
    "Saved 2 product photos",
    "The page is readable",
  ]);
  expect(screen.getByText("Page read")).toBeTruthy();

  const requestsWhenFinished = backend.requests.length;
  await act(() => vi.advanceTimersByTimeAsync(60_000));
  expect(backend.requests).toHaveLength(requestsWhenFinished);
});
