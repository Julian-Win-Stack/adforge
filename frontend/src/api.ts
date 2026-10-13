// The shapes Django's /api/sessions/ endpoints send back (backend/chat/api.py).

/** A file a message carries: a photo the user attached, or something the agent made. A
 * scene's starting picture or clip carries the scene's number; every other file, null. */
export type Attachment = {
  position: number;
  kind: "picture" | "sound" | "video";
  url: string;
  scene: number | null;
};

/** What a message says of a scene's starting picture or clip: a notice that it started, the
 * message showing what it made, or a notice that it ended with nothing made. A scene's
 * picture or clip is being made from its "started" until the next message about it. */
export type SceneStep = {
  scene: number;
  kind: "starting_picture" | "clip";
  state: "started" | "made" | "not_made";
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
};

/** One session, as the sidebar lists it. The name is blank until the first message names it. */
export type Session = {
  id: string;
  name: string;
  created_at: string;
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

/** Say something in a session, with photos when there are any. The server takes it
 * straight away, even while the agent is working. */
export async function sendMessage(sessionId: string, text: string, photos: File[]): Promise<Sent> {
  let request: RequestInit;
  if (photos.length > 0) {
    const form = new FormData();
    form.append("text", text);
    for (const photo of photos) form.append("photos", photo);
    request = { method: "POST", body: form };
  } else {
    request = jsonRequest("POST", { text });
  }
  return readJson<Sent>(await fetch(`/api/sessions/${sessionId}/messages/`, request));
}
