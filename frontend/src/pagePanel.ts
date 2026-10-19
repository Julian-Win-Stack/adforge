import type { Message } from "./api";

/** The page panel as the messages leave it: whether the owner's last pasted page is still
 * being checked, and the one-line reason the last one couldn't be used. */
export type PagePanelState = { checking: boolean; reason: string | null };

/** Whether the page panel is wanted, read from the messages alone: while code's newest word
 * on it (`wants_pasted_page` true on its can't-read notice, false on its notice that a new
 * link was read) wants it, until a pasted page is accepted. A pasted page's result can come
 * on the pasted message itself (as a reopened chat reads it) or on code's notice about it
 * (as an open chat, which fetches each message once, sees it). */
export function pagePanel(messages: Message[]): PagePanelState | null {
  const fromEnd = [...messages]
    .reverse()
    .findIndex((message) => typeof message.wants_pasted_page === "boolean");
  if (fromEnd === -1) return null;
  const asked = messages.length - 1 - fromEnd;
  if (messages[asked].wants_pasted_page !== true) return null;
  let state: PagePanelState = { checking: false, reason: null };
  for (const message of messages.slice(asked + 1)) {
    // The backend sends "" for "no result yet", as on every other message.
    const result = message.pasted_page_result || null;
    if (result === "accepted") return null;
    if (message.pasted_page === true) state = { checking: result === null, reason: result };
    else if (result !== null) state = { checking: false, reason: result };
  }
  return state;
}

/** The owner's newest pasted page, if any. */
export function lastPastedPage(messages: Message[]): Message | null {
  return [...messages].reverse().find((message) => message.pasted_page === true) ?? null;
}

/** The pasted pages the app accepted. Until then a pasted page is code's to check, not the
 * producer's to answer: one the app can't use never reaches it. */
export function acceptedPastes(messages: Message[]): Set<number> {
  const accepted = new Set<number>();
  let last: number | null = null;
  for (const message of messages) {
    if (message.pasted_page === true) last = message.seq;
    if (message.pasted_page_result === "accepted" && last !== null) accepted.add(last);
  }
  return accepted;
}
