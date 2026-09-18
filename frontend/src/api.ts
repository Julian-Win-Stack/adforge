// The shapes Django's /api/jobs/ endpoints send back (backend/jobs/api.py).

export type JobStatus =
  | "queued"
  | "reading_page"
  | "page_read"
  | "planning"
  | "planned"
  | "needs_working_link"
  | "needs_product_photos"
  | "needs_answer"
  | "failed";

export type ActivityEntry = {
  seq: number;
  message: string;
  reason: string;
  created_at: string;
};

export type ProductPhoto = {
  position: number;
  url: string;
  source_url: string;
};

export type Scene = {
  number: number;
  line: string;
  slot_seconds: number;
  status: "planned";
};

/** What the job is waiting for the user to answer. */
export type Question = {
  kind: "working_link" | "product_photos" | "producer";
  question: string;
};

export type Job = {
  id: string;
  product_url: string;
  target_seconds: number | null;
  status: JobStatus;
  brand_colours: string[];
  created_at: string;
};

export type JobWithActivity = Job & {
  photos: ProductPhoto[];
  scenes: Scene[];
  question: Question | null;
  activity: ActivityEntry[];
};

/** Nothing changes on its own after these, so polling stops. The "needs_" statuses wait
 * for the user's answer, and polling starts again once it is sent. */
export const SETTLED: JobStatus[] = [
  "planned",
  "needs_working_link",
  "needs_product_photos",
  "needs_answer",
  "failed",
];

const FORM_FIELDS = ["product_url", "target_seconds"];

export class ApiError extends Error {
  constructor(
    message: string,
    readonly fieldErrors: Record<string, string[]> = {},
  ) {
    super(message);
  }
}

async function readJson<T>(response: Response): Promise<T> {
  if (response.ok) return (await response.json()) as T;
  return refused(response);
}

async function refused(response: Response): Promise<never> {
  const serverSaid = `The server answered ${response.status}.`;
  if (response.status !== 400) throw new ApiError(serverSaid);
  let body: unknown;
  try {
    body = await response.json();
  } catch {
    throw new ApiError(serverSaid);
  }
  if (typeof body !== "object" || body === null) throw new ApiError(serverSaid);
  // Errors about one form field show under it; anything else shows below the form.
  const fieldErrors: Record<string, string[]> = {};
  const otherErrors: string[] = [];
  for (const [key, value] of Object.entries(body)) {
    const messages = (Array.isArray(value) ? value : [value]).map(String);
    if (FORM_FIELDS.includes(key)) fieldErrors[key] = messages;
    else otherErrors.push(...messages);
  }
  throw new ApiError(otherErrors.join(" "), fieldErrors);
}

export async function startJob(productUrl: string, targetSeconds: number | null): Promise<Job> {
  const response = await fetch("/api/jobs/", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ product_url: productUrl, target_seconds: targetSeconds }),
  });
  return readJson<Job>(response);
}

/** The job, plus only the activity entries numbered above `after`. */
export async function getJob(jobId: string, after: number): Promise<JobWithActivity> {
  const response = await fetch(`/api/jobs/${jobId}/?after=${after}`);
  return readJson<JobWithActivity>(response);
}

/** Answers the question the job is waiting on: typed text or a link as `answer`, or
 * photos as an upload. The job carries on by itself once the server has it. */
export async function answerQuestion(jobId: string, answer: string | File[]): Promise<void> {
  let request: RequestInit;
  if (typeof answer === "string") {
    request = {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ answer }),
    };
  } else {
    const form = new FormData();
    for (const photo of answer) form.append("photos", photo);
    request = { method: "POST", body: form };
  }
  const response = await fetch(`/api/jobs/${jobId}/answer/`, request);
  if (!response.ok) await refused(response);
}
