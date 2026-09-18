import { useState, type FormEvent } from "react";
import { ApiError, startJob } from "./api";

export function StartForm({ onStarted }: { onStarted: (jobId: string) => void }) {
  const [productUrl, setProductUrl] = useState("");
  const [targetSeconds, setTargetSeconds] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setSending(true);
    setError(null);
    try {
      const job = await startJob(productUrl, targetSeconds === "" ? null : Number(targetSeconds));
      onStarted(job.id);
    } catch (caught) {
      setError(caught instanceof ApiError ? caught : new ApiError("Could not reach the server."));
    } finally {
      setSending(false);
    }
  }

  return (
    <form onSubmit={submit} style={{ display: "grid", gap: "0.75rem" }}>
      <label>
        Product page link
        <input
          type="url"
          required
          value={productUrl}
          onChange={(e) => setProductUrl(e.target.value)}
          placeholder="https://your-shop.com/products/..."
          style={{ display: "block", width: "100%" }}
        />
        <FieldError messages={error?.fieldErrors.product_url} />
      </label>
      <label>
        Target length in seconds (optional)
        <input
          type="number"
          min={1}
          step={1}
          value={targetSeconds}
          onChange={(e) => setTargetSeconds(e.target.value)}
          style={{ display: "block" }}
        />
        <FieldError messages={error?.fieldErrors.target_seconds} />
      </label>
      <button type="submit" disabled={sending}>
        {sending ? "Starting..." : "Start"}
      </button>
      {error?.message && <p style={{ color: "crimson" }}>{error.message}</p>}
    </form>
  );
}

function FieldError({ messages }: { messages?: string[] }) {
  if (!messages?.length) return null;
  return <span style={{ color: "crimson", display: "block" }}>{messages.join(" ")}</span>;
}
