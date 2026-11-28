import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";
import { Chat } from "./Chat";
import type { Message, PlanCard } from "./api";

const NOW = "2026-11-27T10:00:00Z";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function plan(title: string, seconds = 20) {
  return {
    style: "Person talking",
    seconds,
    title,
    built_on: `What makes ${title} different.`,
    opens: `Opens on ${title}.`,
  };
}

const CARD: PlanCard = {
  id: 7,
  opening: "I read the page and looked at its photos. Here are 3 plans.",
  questions: [
    {
      key: "plan",
      asks: "Which plan?",
      choices: [
        plan("Hot all morning"),
        plan("Fits the cup holder"),
        plan("Ten-year guarantee", 25),
      ],
      pick: 0,
    },
  ],
  longer: "Want 30 seconds? Say so in the chat.",
  state: "open",
  answer: null,
};

function said(role: Message["role"], text: string, more: Partial<Message> = {}): Message {
  return {
    seq: 0,
    role,
    text,
    level: "",
    created_at: NOW,
    attachments: [],
    step: null,
    suggested_note: null,
    ...more,
  };
}

/** Opens a session whose server holds `messages`, and records what the card posts. */
async function session(messages: Message[]) {
  const numbered = messages.map((message, index) => ({ ...message, seq: index + 1 }));
  const posted: { url: string; body: unknown }[] = [];
  vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
    if (init?.method === "POST") {
      posted.push({ url, body: JSON.parse(String(init.body)) });
      const answer = said("user", "Which plan? “Fits the cup holder” (Person talking, 20 s)", {
        seq: numbered.length + 1,
        answers_plan_card: 7,
      });
      return Response.json(
        { session: { id: "session-1", name: "Mug" }, message: answer },
        {
          status: 201,
        },
      );
    }
    const after = Number(new URL(url, "http://x").searchParams.get("after") ?? 0);
    return Response.json(numbered.filter((message) => message.seq > after));
  });
  render(
    <Chat
      sessionId="session-1"
      title="Mug"
      sidebarOpen
      onShowSidebar={() => {}}
      onSent={() => {}}
    />,
  );
  await screen.findByText(numbered[0].text);
  return posted;
}

const LINK = said("user", "Make an ad for https://shop.example/mug");

test("an open card shows 3 plans with the agent's pick chosen and both buttons ready", async () => {
  await session([LINK, said("agent", "", { plan_card: CARD })]);

  const card = screen.getByRole("form", { name: "Plan card" });
  expect(within(card).getByText(CARD.opening)).toBeTruthy();
  const plans = within(card).getByRole("radiogroup", { name: "Which plan?" });
  const options = within(plans).getAllByRole("radio");
  expect(options).toHaveLength(3);
  expect((options[0] as HTMLInputElement).checked).toBe(true);
  expect(within(plans).getAllByText("Person talking · 20 s")).toHaveLength(2);
  expect(within(plans).getByText("Person talking · 25 s")).toBeTruthy();
  expect(within(plans).getByText(/What makes Hot all morning different\./)).toBeTruthy();
  expect(within(plans).getByText(/Opens on Hot all morning\./)).toBeTruthy();
  expect(within(plans).getAllByText("Agent's pick")).toHaveLength(1);
  expect(within(card).getByText(CARD.longer)).toBeTruthy();
  expect(within(card).getByRole("button", { name: "Make this ad" })).toHaveProperty(
    "disabled",
    false,
  );
  expect(document.body.textContent?.toLowerCase()).not.toContain("hero");
});

test("Make this ad sends the plan picked, and the card then takes no more input", async () => {
  const posted = await session([LINK, said("agent", "", { plan_card: CARD })]);

  await userEvent.click(screen.getByRole("radio", { name: /Fits the cup holder/ }));
  await userEvent.click(screen.getByRole("button", { name: "Make this ad" }));

  await waitFor(() => expect(posted).toHaveLength(1));
  expect(posted[0].url).toBe("/api/sessions/session-1/plan-cards/7/answer/");
  expect(posted[0].body).toEqual({ choices: { plan: 1 } });
  await screen.findByText("Which plan? “Fits the cup holder” (Person talking, 20 s)");
  expect(screen.getByRole("button", { name: "Make this ad" })).toHaveProperty("disabled", true);
  expect(screen.getAllByRole("radio").every((radio) => (radio as HTMLInputElement).disabled)).toBe(
    true,
  );
});

test("Use the agent's picks answers the whole card at once", async () => {
  const posted = await session([LINK, said("agent", "", { plan_card: CARD })]);

  await userEvent.click(screen.getByRole("button", { name: "Use the agent's picks" }));

  await waitFor(() => expect(posted).toHaveLength(1));
  expect(posted[0].body).toEqual({ use_picks: true });
});

test("a card answered before the page was opened shows what was chosen", async () => {
  await session([
    LINK,
    said("agent", "", { plan_card: { ...CARD, state: "answered", answer: { plan: 2 } } }),
    said("user", "Which plan? “Ten-year guarantee” (Person talking, 25 s)", {
      answers_plan_card: 7,
    }),
  ]);

  const chosen = screen.getByRole("radio", { name: /Ten-year guarantee/ }) as HTMLInputElement;
  expect(chosen.checked).toBe(true);
  expect(chosen.disabled).toBe(true);
  expect(screen.getByText("Answered")).toBeTruthy();
});

test("a replaced card is greyed with its buttons off, and the newest one is open", async () => {
  const newer = { ...CARD, id: 8, opening: "Here are 3 plans at 30 seconds." };
  await session([
    LINK,
    said("agent", "", { plan_card: CARD }),
    said("user", "Make them 30 seconds"),
    said("agent", "", { plan_card: newer }),
  ]);

  const [old, open] = screen.getAllByRole("form", { name: "Plan card" });
  expect(old.className).toContain("replaced");
  expect(within(old).getByText("Replaced")).toBeTruthy();
  expect(within(old).getByRole("button", { name: "Make this ad" })).toHaveProperty(
    "disabled",
    true,
  );
  expect(within(old).queryByRole("button", { name: "Use the agent's picks" })).toBeNull();
  expect(within(open).getByRole("button", { name: "Make this ad" })).toHaveProperty(
    "disabled",
    false,
  );
});

test("an idea typed in the box takes the place of a plan and is sent with Make this ad", async () => {
  const posted = await session([LINK, said("agent", "", { plan_card: CARD })]);

  const box = screen.getByRole("textbox", { name: "Or tell me what you'd rather have" });
  await userEvent.type(box, "Someone drinking it on the train");

  expect(screen.getAllByRole("radio").some((radio) => (radio as HTMLInputElement).checked)).toBe(
    false,
  );
  await userEvent.click(screen.getByRole("button", { name: "Make this ad" }));
  await waitFor(() => expect(posted).toHaveLength(1));
  expect(posted[0].body).toEqual({ choices: {}, idea: "Someone drinking it on the train" });
});

test("with no plan picked and an empty box, Make this ad waits", async () => {
  await session([LINK, said("agent", "", { plan_card: CARD })]);
  const box = screen.getByRole("textbox", { name: "Or tell me what you'd rather have" });

  await userEvent.type(box, "x");
  await userEvent.clear(box);

  expect(screen.getByRole("button", { name: "Make this ad" })).toHaveProperty("disabled", true);
  // Picking a plan again is the answer, whatever the box holds.
  await userEvent.type(box, "an idea");
  await userEvent.click(screen.getByRole("radio", { name: /Hot all morning/ }));
  expect(screen.getByRole("button", { name: "Make this ad" })).toHaveProperty("disabled", false);
});
