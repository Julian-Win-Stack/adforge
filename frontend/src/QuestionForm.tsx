import { useState, type FormEvent } from "react";
import { ApiError, answerQuestion, type Question } from "./api";

/** The question a job is waiting on, and the form that answers it. Each kind of question
 * takes its own kind of answer: one of its options, typed text, a link, or photos. */
export function QuestionForm({
  jobId,
  question,
  onSent,
}: {
  jobId: string;
  question: Question;
  onSent: (answered: Question) => void;
}) {
  const [text, setText] = useState("");
  const [photos, setPhotos] = useState<File[]>([]);
  const [choice, setChoice] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Picking "use my own line" for a line that failed the fact check asks for the line.
  const writesLine = question.kind === "fact_check" && choice === "own";

  async function send(event: FormEvent) {
    event.preventDefault();
    setSending(true);
    setError(null);
    try {
      if (question.options.length > 0) {
        await answerQuestion(jobId, choice, writesLine ? text : undefined);
      } else {
        await answerQuestion(jobId, question.kind === "product_photos" ? photos : text);
      }
      onSent(question);
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
      {question.options.length > 0 && (
        <fieldset>
          <legend>Your answer</legend>
          {question.options.map((option) => (
            <label key={option.value} style={{ display: "block" }}>
              <input
                type="radio"
                name="choice"
                value={option.value}
                checked={choice === option.value}
                onChange={() => setChoice(option.value)}
              />
              {option.label}
            </label>
          ))}
        </fieldset>
      )}
      {writesLine && (
        <label>
          Your line
          <textarea required value={text} onChange={(e) => setText(e.target.value)} />
        </label>
      )}
      {(question.kind === "producer" || question.kind === "unclear_page") && (
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
            accept="image/png,image/jpeg,image/webp,image/gif"
            multiple
            onChange={(e) => setPhotos([...(e.target.files ?? [])])}
          />
        </label>
      )}
      <button
        type="submit"
        disabled={
          sending ||
          (question.kind === "product_photos" && photos.length === 0) ||
          (question.options.length > 0 && choice === "")
        }
      >
        Send answer
      </button>
      {error && <p style={{ color: "crimson" }}>{error}</p>}
    </form>
  );
}
