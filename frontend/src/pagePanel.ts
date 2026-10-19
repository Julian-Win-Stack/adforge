import type { Message } from "./api";

/** The page panel as the messages leave it: whether the owner's last pasted page is still
 * being checked, and the one-line reason the last one couldn't be used. */
export type PagePanelState = { checking: boolean; reason: string | null };

const LINK = /https?:\/\/\S+/i;

/** Whether the page panel is wanted, read from the messages alone: from code's newest notice
 * that the page can't be read, until a pasted page is accepted or a new link is read. A
 * pasted page's result can come on the pasted message itself (as a reopened chat reads it)
 * or on code's notice about it (as an open chat, which fetches each message once, sees it).
 * A link the owner sends is read when the producer next answers: had it failed, the read
 * would have posted a newer can't-read notice first. */
export function pagePanel(messages: Message[]): PagePanelState | null {
  const fromEnd = [...messages].reverse().findIndex((message) => message.wants_pasted_page);
  if (fromEnd === -1) return null;
  const asked = messages.length - 1 - fromEnd;
  let state: PagePanelState = { checking: false, reason: null };
  let linkSent = false;
  for (const message of messages.slice(asked + 1)) {
    const result = message.pasted_page_result ?? null;
    if (result === "accepted") return null;
    if (message.pasted_page) state = { checking: result === null, reason: result };
    else if (result !== null) state = { checking: false, reason: result };
    else if (message.role === "user" && LINK.test(message.text)) linkSent = true;
    else if (message.role === "agent" && linkSent) return null;
  }
  return state;
}
