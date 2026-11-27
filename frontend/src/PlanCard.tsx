import { useState, type FormEvent, type ReactNode } from "react";
import { ApiError, type CardQuestion, type PlanCard, type PlanCardAnswer } from "./api";

/** The plan card the agent shows once the page is read (backend agents/plan_card.py): 1 to 3
 * questions, each a set of radio choices with the agent's pick chosen to start with, then
 * "Use the agent's picks" and "Make this ad". Under the plans a box takes the shop owner's own
 * idea instead of a plan: typing in it unpicks the plan, and picking a plan again is the
 * answer whatever it holds. Nothing is made until it is answered. Once answered or replaced
 * it shows how it ended and takes no input. */
export function PlanCardView({
  card,
  state,
  answer,
  onAnswer,
}: {
  card: PlanCard;
  /** How the card stands as the chat sees it, which may be newer than `card.state`. */
  state: PlanCard["state"];
  /** What was chosen once answered (`PlanCard.answer`), or null. */
  answer: PlanCard["answer"];
  onAnswer: (answer: PlanCardAnswer) => Promise<void>;
}) {
  const [chosen, setChosen] = useState<Record<string, number>>(() =>
    Object.fromEntries(card.questions.map((question) => [question.key, question.pick])),
  );
  const [idea, setIdea] = useState("");
  const [sending, setSending] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const closed = state !== "open" || sending;
  const shownChoice = (key: string) => {
    const index = answer ? answer[key] : chosen[key];
    return typeof index === "number" ? index : undefined;
  };
  const shownIdea = answer ? String(answer.idea ?? "") : idea;
  const ready = chosen.plan !== undefined || idea.trim() !== "";

  function typeIdea(text: string) {
    setIdea(text);
    if (text.trim())
      setChosen((was) => Object.fromEntries(Object.entries(was).filter(([key]) => key !== "plan")));
  }

  async function send(sent: PlanCardAnswer) {
    setSending(true);
    setProblem(null);
    try {
      await onAnswer(sent);
    } catch (error) {
      setProblem(error instanceof ApiError ? error.message : "That didn't go through. Try again.");
      setSending(false);
    }
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    if (!ready) return;
    send(chosen.plan !== undefined ? { choices: chosen } : { choices: chosen, idea: idea.trim() });
  }

  return (
    <form
      className={`plan-card${state === "replaced" ? " replaced" : ""}`}
      aria-label="Plan card"
      onSubmit={submit}
    >
      <p className="plan-card-opening">{card.opening}</p>
      {card.questions.map((question) => (
        <Question
          key={question.key}
          question={question}
          chosen={shownChoice(question.key)}
          disabled={closed}
          onChoose={(index) => setChosen((was) => ({ ...was, [question.key]: index }))}
          after={
            question.key === "plan" && (
              <textarea
                className="plan-card-idea"
                aria-label="Or tell me what you'd rather have"
                placeholder="Or tell me what you'd rather have…"
                rows={2}
                value={shownIdea}
                disabled={closed}
                onChange={(event) => typeIdea(event.target.value)}
              />
            )
          }
        />
      ))}
      <p className="plan-card-longer">{card.longer}</p>
      {problem && (
        <p role="alert" className="plan-card-problem">
          {problem}
        </p>
      )}
      {state === "open" ? (
        <div className="plan-card-buttons">
          <button type="button" disabled={closed} onClick={() => send({ use_picks: true })}>
            Use the agent's picks
          </button>
          <button type="submit" className="go" disabled={closed || !ready}>
            Make this ad
          </button>
        </div>
      ) : (
        <div className="plan-card-buttons">
          <span className="plan-card-state">{state === "answered" ? "Answered" : "Replaced"}</span>
          <button type="submit" className="go" disabled>
            Make this ad
          </button>
        </div>
      )}
    </form>
  );
}

function Question({
  question,
  chosen,
  disabled,
  onChoose,
  after,
}: {
  question: CardQuestion;
  chosen: number | undefined;
  disabled: boolean;
  onChoose: (index: number) => void;
  /** What follows its choices, such as the box for the shop owner's own idea. */
  after?: ReactNode;
}) {
  const name = `plan-card-${question.key}`;
  return (
    <fieldset className="plan-card-question" disabled={disabled}>
      <legend id={`${name}-asks`}>{question.asks}</legend>
      <div
        role="radiogroup"
        aria-labelledby={`${name}-asks`}
        className={question.key === "plan" ? "plan-card-plans" : "plan-card-chips"}
      >
        {question.choices.map((choice, index) => {
          const pick = index === question.pick && (
            <span className="plan-card-pick">Agent's pick</span>
          );
          const input = (
            <input
              type="radio"
              name={name}
              checked={chosen === index}
              disabled={disabled}
              onChange={() => onChoose(index)}
            />
          );
          if (typeof choice === "string") {
            return (
              <label key={index} className="plan-card-chip">
                {input}
                {choice}
                {pick}
              </label>
            );
          }
          return (
            <label key={index} className="plan-card-plan">
              {input}
              <span className="plan-card-plan-head">
                <span className="plan-card-style">
                  {choice.style} · {choice.seconds} s
                </span>
                {pick}
              </span>
              <b>“{choice.title}”</b>
              <span className="plan-card-line">Built on: {choice.built_on}</span>
              <span className="plan-card-line">{choice.opens}</span>
            </label>
          );
        })}
      </div>
      {after}
    </fieldset>
  );
}
