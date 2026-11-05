import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";
import { Chat } from "./Chat";
import type { CardVersion, Message } from "./api";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

async function open(verdict: CardVersion["verdict"] = "unsure", sends = false) {
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
      const data = JSON.parse(String(init?.body));
      checked.push(data);
      // A Looks wrong note is sent as the owner's message, as chat/api.py does.
      const message: Message | null =
        sends && data.note
          ? {
              seq: messages.length + 1,
              role: "user",
              level: "",
              text: `Scene 2: ${data.note}`,
              created_at: "2026-11-04T10:00:04Z",
              attachments: [],
              step: null,
              suggested_note: null,
            }
          : null;
      if (message) messages.push(message);
      return Response.json({
        scene: 2,
        versions: versions.map((one) => ({ ...one, owner_check: data.choice })),
        sent: message && { session: { id: "session-1", name: "Mug", created_at: "" }, message },
      });
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

test.each(["right", "wrong"] as const)(
  "an optional Looks %s check records the choice and updates the watch line",
  async (choice) => {
    const { checked, user } = await open();
    await screen.findByRole("button", { name: "Looks right" });
    if (choice === "wrong")
      await user.type(
        screen.getByRole("textbox", { name: "Note for scene 2" }),
        "Show the other side.",
      );
    await user.click(screen.getByRole("button", { name: `Looks ${choice}` }));
    expect(checked).toEqual([
      { clip: 14, choice, note: choice === "wrong" ? "Show the other side." : "" },
    ]);
    expect(await screen.findByText(`You checked it: looks ${choice}.`)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Looks right" })).toBeNull();
    expect(
      within(screen.getByRole("article", { name: "Scene 2" })).getByText(
        `You said it looks ${choice}.`,
      ),
    ).toBeTruthy();
    expect(screen.getByRole("textbox", { name: "Note for scene 2" })).toBeTruthy();
  },
);

test.each(["pass", "fail"] as const)(
  "a clip with verdict %s offers no owner check",
  async (verdict) => {
    const { checked } = await open(verdict);
    expect(screen.queryByRole("button", { name: "Looks right" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Looks wrong" })).toBeNull();
    expect(checked).toEqual([]);
  },
);

test("a message posted just before a Looks wrong note is sent still shows, in its place", async () => {
  const { messages, user } = await open("unsure", true);
  await screen.findByRole("button", { name: "Looks wrong" });
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
  await user.click(screen.getByRole("button", { name: "Looks wrong" }));

  expect(await screen.findByText("Making scene 3's clip.")).toBeTruthy();
  const said = within(screen.getByRole("list", { name: "Conversation" }))
    .getAllByRole("listitem")
    .map((item) => item.textContent);
  expect(said.slice(-2)).toEqual(["Making scene 3's clip.", "YouScene 2: Show the other side."]);
});
