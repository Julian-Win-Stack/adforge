import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";
import { Chat } from "./Chat";
import type { CardVersion, Message } from "./api";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

async function open(verdict: CardVersion["verdict"] = "unsure") {
  const messages: Message[] = [
    {
      seq: 1,
      role: "user",
      level: "",
      text: "https://shop.example/mug",
      created_at: "2026-11-04T10:00:00Z",
      attachments: [],
      step: null,
      suggested_note: null,
    },
    {
      seq: 2,
      role: "agent",
      level: "",
      text: "Here's the clip",
      created_at: "2026-11-04T10:00:01Z",
      attachments: [{ position: 1, kind: "video", url: "/clip.mp4", scene: 2, version: "v1" }],
      step: null,
      suggested_note: null,
    },
    {
      seq: 3,
      role: "notice",
      level: "info",
      text: "Please watch scene 2's clip: the check couldn't tell.",
      created_at: "2026-11-04T10:00:02Z",
      attachments: [],
      step: null,
      suggested_note: null,
      watch: { scene: 2, clip: 14 },
    },
  ];
  const versions: CardVersion[] = [
    {
      clip: 14,
      number: 1,
      label: "v1",
      url: "/clip.mp4",
      picture: null,
      verdict,
      problem: "",
      in_ad: true,
      free: false,
    },
  ];
  const checked: object[] = [];
  vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
    if (url.endsWith("/check/")) {
      checked.push(JSON.parse(String(init?.body)));
      throw new Error("Owner check buttons should not submit a choice.");
    }
    if (url.endsWith("/messages/") && init?.method === "POST") {
      const { text } = JSON.parse(String(init.body));
      const message: Message = {
        seq: messages.length + 1,
        role: "user",
        level: "",
        text,
        created_at: "2026-11-04T10:00:04Z",
        attachments: [],
        step: null,
        suggested_note: null,
      };
      messages.push(message);
      return Response.json({ session: { id: "session-1", name: "Mug", created_at: "" }, message });
    }
    if (url.endsWith("/scenes/")) return Response.json([{ scene: 2, versions }]);
    // A poll gives what came after the number it names, as chat/api.py does.
    const after = Number(new URL(url, "http://test").searchParams.get("after") ?? 0);
    return Response.json(messages.filter((message) => message.seq > after));
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
  await screen.findByText("https://shop.example/mug");
  return { checked, messages, user: userEvent.setup() };
}

test.each(["unsure", "not_checked", "pass", "fail"] as const)(
  "a clip with verdict %s keeps its note field without owner check buttons",
  async (verdict) => {
    const { checked, user } = await open(verdict);
    // Wait for the scene's versions to load, then open the exact clip from the notice.
    await screen.findByText("v1");
    await user.click(screen.getByRole("button", { name: /Please watch scene 2/ }));
    const card = within(screen.getByRole("article", { name: "Scene 2" }));
    expect(card.getByRole("textbox", { name: "Note for scene 2" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Looks right" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Looks wrong" })).toBeNull();
    expect(checked).toEqual([]);
  },
);

test("a message posted just before a scene note is sent still shows, in its place", async () => {
  const { messages, user } = await open("unsure");
  await screen.findByRole("textbox", { name: "Note for scene 2" });
  // Code posts a notice after the last poll, and the owner's note goes before the next one.
  messages.push({
    seq: 4,
    role: "notice",
    level: "info",
    text: "Making scene 3's clip.",
    created_at: "2026-11-04T10:00:03Z",
    attachments: [],
    step: null,
    suggested_note: null,
  });
  await user.type(
    screen.getByRole("textbox", { name: "Note for scene 2" }),
    "Show the other side.",
  );
  await user.click(screen.getByRole("button", { name: "Send 1 note" }));

  expect(await screen.findByText("Making scene 3's clip.")).toBeTruthy();
  const said = within(screen.getByRole("list", { name: "Conversation" }))
    .getAllByRole("listitem")
    .map((item) => item.textContent);
  expect(said.slice(-2)).toEqual(["Making scene 3's clip.", "YouScene 2: Show the other side."]);
});
