import { useEffect, useState } from "react";
import { SETTLED, getJob, type ActivityEntry, type JobWithActivity } from "./api";

const POLL_EVERY_MS = 2000;

/** Shows a job's activity as it happens. Each poll asks only for entries after the
 * last one already on screen, so nothing is skipped or shown twice. Render it with
 * `key={jobId}` so a different job starts from an empty list. */
export function JobView({ jobId }: { jobId: string }) {
  const [job, setJob] = useState<JobWithActivity | null>(null);
  const [activity, setActivity] = useState<ActivityEntry[]>([]);
  const [pollError, setPollError] = useState<string | null>(null);

  useEffect(() => {
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    let lastSeq = 0;

    async function poll() {
      try {
        const latest = await getJob(jobId, lastSeq);
        if (stopped) return;
        if (latest.activity.length > 0) {
          lastSeq = latest.activity[latest.activity.length - 1].seq;
          setActivity((shown) => [...shown, ...latest.activity]);
        }
        setJob(latest);
        setPollError(null);
        // A settled job's last entry is already in this response, so we can stop.
        if (SETTLED.includes(latest.status)) return;
      } catch {
        if (stopped) return;
        setPollError("Lost contact with the server. Still trying...");
      }
      timer = setTimeout(poll, POLL_EVERY_MS);
    }

    poll();
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
  }, [jobId]);

  if (job === null) return <p>{pollError ?? "Loading..."}</p>;

  return (
    <section>
      <p>
        <strong>{job.product_url}</strong>
        {job.target_seconds !== null && ` · target ${job.target_seconds}s`}
        {" · "}
        <StatusLabel status={job.status} />
      </p>
      {pollError && <p style={{ color: "crimson" }}>{pollError}</p>}

      <h2>Activity</h2>
      <ol>
        {activity.map((entry) => (
          <li key={entry.seq} style={{ marginBottom: "0.5rem" }}>
            <div>{entry.message}</div>
            <div style={{ color: "#555", fontSize: "0.9em" }}>Why: {entry.reason}</div>
          </li>
        ))}
      </ol>

      {job.photos.length > 0 && (
        <>
          <h2>Product photos</h2>
          <div style={{ display: "flex", flexWrap: "wrap", gap: "0.5rem" }}>
            {job.photos.map((photo) => (
              <img
                key={photo.position}
                src={photo.url}
                alt={`Product photo ${photo.position}`}
                style={{ width: 120, height: 120, objectFit: "cover" }}
              />
            ))}
          </div>
        </>
      )}
    </section>
  );
}

const STATUS_LABELS = {
  queued: "Waiting to start",
  reading_page: "Reading the page...",
  page_read: "Page read",
  needs_working_link: "Waiting for a working link",
  needs_product_photos: "Waiting for product photos",
  failed: "Failed",
} as const;

const STATUS_COLOURS: Partial<Record<JobWithActivity["status"], string>> = {
  needs_working_link: "darkorange",
  needs_product_photos: "darkorange",
  failed: "crimson",
};

function StatusLabel({ status }: { status: JobWithActivity["status"] }) {
  return <span style={{ color: STATUS_COLOURS[status] }}>{STATUS_LABELS[status]}</span>;
}
