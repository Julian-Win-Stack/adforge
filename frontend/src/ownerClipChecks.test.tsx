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
      const data = JSON.parse(String(init?.body));
      checked.push(data);
      return Response.json({
        scene: 2,
        versions: versions.map((one) => ({ ...one, owner_check: data.choice })),
        sent: null,
      });
    }
    return Response.json(url.endsWith("/scenes/") ? [{ scene: 2, versions }] : messages);
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
  return { checked, user: userEvent.setup() };
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
