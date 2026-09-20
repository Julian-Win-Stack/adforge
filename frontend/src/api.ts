// The shapes Django's /api/sessions/ endpoints send back (backend/chat/api.py).

/** A file a message carries: a photo the user attached, or something the agent made. */
export type Attachment = {
  position: number;
  kind: "picture" | "sound" | "video";
  url: string;
};

/** One turn in a session, from the user or the agent. */
export type Message = {
  seq: number;
  role: "user" | "agent";
  text: string;
  created_at: string;
  attachments: Attachment[];
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
  if (response.status !== 400) throw new ApiError(serverSaid);
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
