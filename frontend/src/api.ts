// The shapes Django's /api/jobs/ endpoints send back (backend/jobs/api.py).

export type JobStatus = "queued" | "reading_page" | "page_read" | "failed";

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

export type Job = {
  id: string;
  product_url: string;
  target_seconds: number | null;
  status: JobStatus;
  created_at: string;
};

export type JobWithActivity = Job & {
  photos: ProductPhoto[];
  activity: ActivityEntry[];
};

export const FINISHED: JobStatus[] = ["page_read", "failed"];

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
  if (response.status === 400) {
    throw new ApiError("Please check the form.", await response.json());
  }
  throw new ApiError(`The server answered ${response.status}.`);
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
