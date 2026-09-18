import { useState, type FormEvent } from "react";
import { ApiError, answerQuestion, type Question } from "./api";

/** The question a job is waiting on, and the form that answers it. Each kind of question
 * takes its own kind of answer: typed text, a link, or photos. */
export function QuestionForm({
  jobId,
  question,
  onSent,
}: {
  jobId: string;
  question: Question;
  onSent: () => void;
}) {
  const [text, setText] = useState("");
  const [photos, setPhotos] = useState<File[]>([]);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function send(event: FormEvent) {
    event.preventDefault();
    setSending(true);
    setError(null);
    try {
      await answerQuestion(jobId, question.kind === "product_photos" ? photos : text);
      onSent();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Couldn't reach the server. Try again.");
    } finally {
      setSending(false);
    }
  }

  return (
    <form onSubmit={send} style={{ border: "2px solid darkorange", padding: "0.75rem" }}>
      <p>
        <strong>{question.question}</strong>
      </p>
      {question.kind === "producer" && (
        <label>
          Your answer
          <textarea required value={text} onChange={(e) => setText(e.target.value)} />
        </label>
      )}
      {question.kind === "working_link" && (
        <label>
          Working link
          <input type="url" required value={text} onChange={(e) => setText(e.target.value)} />
        </label>
      )}
      {question.kind === "product_photos" && (
        <label>
          Product photos
          <input
            type="file"
            accept="image/*"
            multiple
            onChange={(e) => setPhotos([...(e.target.files ?? [])])}
          />
        </label>
      )}
      <button
        type="submit"
        disabled={sending || (question.kind === "product_photos" && photos.length === 0)}
      >
        Send answer
      </button>
      {error && <p style={{ color: "crimson" }}>{error}</p>}
    </form>
  );
}
