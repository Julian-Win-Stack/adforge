// The shapes Django's /api/sessions/ endpoints send back (backend/chat/api.py).

/** A file a message carries: a photo the user attached, or something the agent made. A
 * scene's starting picture or clip carries the scene's number; every other file, null. */
export type Attachment = {
  position: number;
  kind: "picture" | "sound" | "video";
  url: string;
  scene: number | null;
  /** For a scene's starting picture or clip, the label of the scene version it is shown as,
   * such as "v2 black toilet"; blank otherwise. */
  version?: string;
  /** For a scene's clip, what was sent to the video API to make it; the empty list for a
   * clip made before anything was recorded. Null (or absent) on any other file. */
  sent?: SentList | null;
};

/** A picture sent with a clip or left out of it (backend jobs/library.py `PictureRef`): a
 * library item, or one drawn for the clip (tier 3). `url` is a card-size copy of `file`. */
export type PictureRef = {
  tier: 1 | 2 | 3 | 4;
  library_number: number | null;
  drawn: string | null;
  for: string | null;
  label: string;
  file: string;
  url: string;
};

/** A picture sent with a clip, as Image N. */
export type SentPicture = PictureRef & { image: number };

/** A piece of one of the product's videos sent with a clip, as Video N: its moment's
 * seconds, and the seconds sent, inside them when it was trimmed to fit. */
export type SentPiece = {
  number: number;
  video: number;
  moment: number;
  moment_start: number;
  moment_end: number;
  start: number;
  end: number;
  trim: string | null;
  file: string;
  url: string;
};

/** A library item the clip's colour was put on, and whether that worked or the item went
 * in its own colour. */
export type SentRecolour = {
  of: number;
  colour: string;
  outcome: "done" | "fell_back";
  made: number | null;
};

/** What one clip was made from (backend jobs/library.py `SentList`): the pictures in the
 * order sent (Image 1, Image 2, ...), the pieces, the pictures that didn't fit, the
 * recolours, and the words sent with them. */
export type SentList = {
  colour: string;
  pictures: SentPicture[];
  pieces: SentPiece[];
  left_out: PictureRef[];
  recolours: SentRecolour[];
  prompt: string;
};

/** What a message says of a scene's starting picture or clip: a notice that it started, the
 * message showing what it made, or a notice that it ended with nothing made. A scene's
 * picture or clip is being made from its "started" until the next message about it. */
export type SceneStep = {
  scene: number;
  kind: "starting_picture" | "clip";
  state: "started" | "made" | "not_made";
};

/** An owner photo a plan's scene uses: the one thing taken from it, whether that thing is in
 * the scene's starting picture now, and whether more of it shows during the action. */
export type PlanPhoto = {
  photo: number;
  thing: string;
  in_picture: boolean;
  during_action: boolean;
};

/** What a fix plan changes in one scene, or why it can't. Amounts are dollars as strings. */
export type PlanScene = {
  scene: number;
  action: "fix" | "new_picture" | "new_clip" | "new_line" | "go_back" | "no_change";
  change: string;
  line: string;
  /** The scene's new overlay, for a new line that changes what it shows. Blank keeps it. */
  overlay: string;
  go_back_to: number | null;
  cant: string;
  estimate_usd: string;
  estimate_up_to_usd: string;
  photos: PlanPhoto[];
};

/** A fix plan as code posts it for the owner's yes (backend jobs/fix_plans.py `notice`),
 * read as it stands when the message was fetched. */
export type PlanNotice = {
  plan: number;
  state: "proposed" | "confirmed" | "declined" | "replaced";
  question: string;
  estimate_usd: string;
  estimate_up_to_usd: string;
  /** What the owner asked that can't be done and isn't about one scene (music, voice...). */
  cants: { kind: string; text: string }[];
  scenes: PlanScene[];
};

/** What a message says of a fix plan: code's notice showing it (with the plan), the owner's
 * tap of its yes button, or code's notice that it is going ahead or was dropped. */
export type MessagePlan = {
  id: number;
  event: "shown" | "yes" | "confirmed" | "declined";
  notice: PlanNotice | null;
};

/** One try of a clip the clip judge failed every try of: its "Try 1" name, its scene
 * version label, and what the judge still sees wrong in it. */
export type TryClip = {
  clip: number;
  name: string;
  version: string;
  url: string;
  problem: string;
};

/** Every try of a scene's clip the clip judge failed every try of, for the owner to pick
 * from, and the one in the ad when the notice was read (null when none is). `on_offer` says
 * whether a try can be swapped in then: one is in the ad, or the app gave up on the scene
 * and none is yet. `picked` is the try the app would pick, and `reason` why it gave up. */
export type MessageTries = {
  scene: number;
  in_ad: number | null;
  on_offer?: boolean;
  picked?: number | null;
  reason?: string;
  clips: TryClip[];
};

/** One turn in a session, from the user or the agent, or a notice the server posts when a
 * step fell back or failed, or a scene's picture or clip started. Only a notice has a level:
 * red for a problem, grey for info. A message about a scene's picture or clip has its step. */
export type Message = {
  seq: number;
  role: "user" | "agent" | "notice";
  level: "problem" | "info" | "";
  text: string;
  created_at: string;
  attachments: Attachment[];
  step: SceneStep | null;
  /** A note the message suggests for a scene's card, as when the check of a fix found the
   * change missing; null for none. */
  suggested_note: { scene: number; text: string } | null;
  /** The fix plan the message is about; null for none. */
  plan: MessagePlan | null;
  /** Code's notice showing every try of a clip the judge failed every try of; null for any
   * other message. */
  tries?: MessageTries | null;
  /** Code's red line that the fixing agent asked the shop owner a question about a scene:
   * the scene, the agent's words, and whether it still waits for its answer as the message
   * was read. The answer comes through the producer, never the card. Null on any other
   * message. */
  question?: { scene: number; text: string; open: boolean } | null;
  /** The try the owner's tap of "Use this one" chose; null for any other message. */
  use_clip?: number | null;
  /** True on code's notice that the page can't be read: the page panel is wanted, for the
   * shop owner to paste the page text and attach its photos. False on code's notice that a
   * new link was read while it was wanted. Null on any other message. */
  wants_pasted_page?: boolean | null;
  /** The shop owner's page text and photos, sent from the page panel, never typed. */
  pasted_page?: boolean;
  /** What code made of a pasted page: "accepted", or the one-line reason it can't be used.
   * On the pasted message once it is checked, and on code's notice saying so. Blank ("", or
   * null) while it is being checked, and on any other message. */
  pasted_page_result?: string | null;
  /** True on code's notice that a pasted page was kept with too few photos: the page panel
   * stays open for more photos until the shop owner answers. */
  wants_more_photos?: boolean;
};

/** One session, as the sidebar lists it. The name is blank until the first message names it. */
export type Session = {
  id: string;
  name: string;
  created_at: string;
  /** Whether the owner's pause holds: until their next message. Only the session itself
   * says it; the list may leave it out. */
  paused?: boolean;
};

/** What the server answers when a message is sent: the message, and the session it landed
 * in, whose name the first message may just have set. */
export type Sent = { session: Session; message: Message };

/** A request the server refused. The message is the server's reason, ready to show. */
export class ApiError extends Error {}

async function readJson<T>(response: Response): Promise<T> {
  if (response.ok) return (await response.json()) as T;
  return refused(response);
}

async function refused(response: Response): Promise<never> {
  const serverSaid = `The server answered ${response.status}.`;
  // A 400 is something wrong with what was sent, and a 409 another session making an ad:
  // both come with a reason to show.
  if (response.status !== 400 && response.status !== 409) throw new ApiError(serverSaid);
  let body: unknown;
  try {
    body = await response.json();
  } catch {
    throw new ApiError(serverSaid);
  }
  if (typeof body !== "object" || body === null) throw new ApiError(serverSaid);
  // Every reason the server gave, whichever field it was about: the text, the photos or
  // a session's name.
  const reasons = Object.values(body)
    .flatMap((value) => (Array.isArray(value) ? value : [value]))
    .map(String);
  throw new ApiError(reasons.join(" ") || serverSaid);
}

function jsonRequest(method: "POST" | "PATCH", body: unknown): RequestInit {
  return { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
}

/** Every session, newest first. */
export async function listSessions(): Promise<Session[]> {
  return readJson<Session[]>(await fetch("/api/sessions/"));
}

export async function startSession(): Promise<Session> {
  return readJson<Session>(await fetch("/api/sessions/", jsonRequest("POST", {})));
}

export async function renameSession(sessionId: string, name: string): Promise<Session> {
  return readJson<Session>(
    await fetch(`/api/sessions/${sessionId}/`, jsonRequest("PATCH", { name })),
  );
}

/** Only the messages numbered above `after`, so a poll never repeats or skips one. */
export async function getMessagesSince(sessionId: string, after: number): Promise<Message[]> {
  return readJson<Message[]>(await fetch(`/api/sessions/${sessionId}/messages/?after=${after}`));
}

/** What a message is besides its words: a tap of a plan's yes button names the `plan`, and
 * a tap of "Use this one" under a try names the try, `useClip`; the page panel's Done sends
 * the page text and photos as a `pastedPage`. Each marks the message as never typed. */
export type SendAs = { plan?: number; useClip?: number; pastedPage?: boolean };

/** Say something in a session, with photos when there are any. The server takes it
 * straight away, even while the agent is working. */
export async function sendMessage(
  sessionId: string,
  text: string,
  photos: File[],
  { plan, useClip, pastedPage }: SendAs = {},
): Promise<Sent> {
  let request: RequestInit;
  if (photos.length > 0 || pastedPage) {
    const form = new FormData();
    form.append("text", text);
    for (const photo of photos) form.append("photos", photo);
    if (pastedPage) form.append("pasted_page", "true");
    request = { method: "POST", body: form };
  } else {
    request = jsonRequest(
      "POST",
      useClip !== undefined
        ? { text, use_clip: useClip }
        : plan === undefined
          ? { text }
          : { text, plan },
    );
  }
  return readJson<Sent>(await fetch(`/api/sessions/${sessionId}/messages/`, request));
}

/** What the pause notice says, as agents/pause.py posts it (PAUSED_NOTICE): the chat is
 * paused from that notice until the owner's next message. */
export const PAUSED_NOTICE = "Paused. Nothing new is started until you write.";

/** The pause button: the producer starts nothing new until the owner writes. Steps waiting
 * to start are cancelled and the running ones finish; the chat is told by a notice. */
export async function pauseSession(sessionId: string): Promise<void> {
  await readJson<{ paused: boolean }>(
    await fetch(`/api/sessions/${sessionId}/pause/`, jsonRequest("POST", {})),
  );
}
