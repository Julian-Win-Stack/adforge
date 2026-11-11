import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { App } from "./App";
import type { Folder, Session } from "./api";

const NOW = "2026-09-20T10:00:00Z";

/** Stands in for Django's /api/sessions/ and /api/folders/ endpoints, as chat/api.py keeps
 * them: a session is in one folder or none, a folder name is taken once whatever its case,
 * and a removed folder's sessions are kept in none. */
function fakeBackend() {
  const sessions: Session[] = [];
  const folders: Folder[] = [];
  const requests: { method: string; url: string; body?: unknown }[] = [];

  vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    requests.push({ method, url, body });

    if (url === "/api/sessions/" && method === "GET") return Response.json(sessions);
    if (url === "/api/folders/") {
      if (method === "GET") return Response.json(folders);
      const name = String(body.name).trim();
      const clash = folders.find((one) => one.name.toLowerCase() === name.toLowerCase());
      if (clash) {
        return Response.json(
          { name: [`There's already a folder called ${clash.name}.`] },
          { status: 400 },
        );
      }
      const folder = { id: `folder-${folders.length + 1}`, name, created_at: NOW };
      folders.push(folder);
      return Response.json(folder, { status: 201 });
    }
    const folderUrl = url.match(/^\/api\/folders\/([\w-]+)\/$/);
    if (folderUrl) {
      const index = folders.findIndex((one) => one.id === folderUrl[1]);
      if (method === "DELETE") {
        folders.splice(index, 1);
        for (const session of sessions) {
          if (session.folder === folderUrl[1]) session.folder = null;
        }
        return new Response(null, { status: 204 });
      }
      folders[index] = { ...folders[index], name: body.name };
      return Response.json(folders[index]);
    }
    const sessionUrl = url.match(/^\/api\/sessions\/([\w-]+)\/$/);
    if (sessionUrl && method === "PATCH") {
      const session = sessions.find((one) => one.id === sessionUrl[1])!;
      Object.assign(session, body);
      return Response.json(session);
    }
    if (url.match(/^\/api\/sessions\/[\w-]+\/(messages\/\?after=\d+|scenes\/)$/)) {
      return Response.json([]);
    }
    throw new Error(`Unexpected request: ${method} ${url}`);
  });

  return {
    requests,
    folder(name: string) {
      const folder = { id: `folder-${folders.length + 1}`, name, created_at: NOW };
      folders.push(folder);
      return folder.id;
    },
    /** A session named so, in the folder given; each one added is newer than the last. */
    session(name: string, folder: string | null = null) {
      const session = { id: `session-${sessions.length + 1}`, name, created_at: NOW, folder };
      sessions.unshift(session);
      return session.id;
    },
    folderOf(id: string) {
      return sessions.find((one) => one.id === id)!.folder;
    },
  };
}

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  vi.setSystemTime(new Date(NOW));
  localStorage.clear();
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

function sidebar() {
  return within(screen.getByRole("navigation", { name: "Sessions" }));
}

/** The names of the sessions shown outside any folder, top to bottom. */
function looseSessions() {
  return within(screen.getByRole("region", { name: "Not in a folder" }))
    .queryAllByRole("listitem")
    .map((item) => item.querySelector("button")?.textContent);
}

/** The folders, top to bottom, as each one's toggle names it. */
function folderNames() {
  return within(screen.getByRole("list", { name: "Folders" }))
    .getAllByRole("button", { name: /, \d+ sessions?$/ })
    .map((button) => button.getAttribute("aria-label"));
}

function setup() {
  return userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
}

test("folders are listed in number order, closed, each saying how many sessions it holds", async () => {
  const backend = fakeBackend();
  const round10 = backend.folder("Round 10");
  const round2 = backend.folder("Round 2");
  backend.session("Mug ad", round2);
  backend.session("Kettle ad", round2);
  backend.session("Lamp ad", round10);
  backend.session("Teapot ad");
  render(<App />);

  await sidebar().findByRole("button", { name: "Round 2, 2 sessions" });
  expect(folderNames()).toEqual(["Round 2, 2 sessions", "Round 10, 1 session"]);
  expect(sidebar().queryByRole("button", { name: "Mug ad" })).toBeNull();
  expect(looseSessions()).toEqual(["Teapot ad"]);
});

test("a folder opens to show its sessions, newest first, and closes again", async () => {
  const backend = fakeBackend();
  const round2 = backend.folder("Round 2");
  backend.session("Mug ad", round2);
  backend.session("Kettle ad", round2);
  const user = setup();
  render(<App />);

  const toggle = await sidebar().findByRole("button", { name: "Round 2, 2 sessions" });
  await user.click(toggle);

  expect(toggle.getAttribute("aria-expanded")).toBe("true");
  const inside = within(screen.getByRole("list", { name: "Round 2" }));
  expect(inside.getAllByRole("button", { name: /ad$/ }).map((b) => b.textContent)).toEqual([
    "Kettle ad",
    "Mug ad",
  ]);

  await user.click(toggle);
  expect(screen.queryByRole("list", { name: "Round 2" })).toBeNull();
});

test("a folder stays open after the page is loaded again", async () => {
  const backend = fakeBackend();
  const round2 = backend.folder("Round 2");
  backend.session("Mug ad", round2);
  const user = setup();
  const { unmount } = render(<App />);
  await user.click(await sidebar().findByRole("button", { name: "Round 2, 1 session" }));
  unmount();

  render(<App />);

  await screen.findByRole("list", { name: "Round 2" });
});

test("makes a new named folder", async () => {
  const backend = fakeBackend();
  backend.session("Mug ad");
  const user = setup();
  render(<App />);

  await user.click(await sidebar().findByRole("button", { name: "New folder" }));
  await user.type(screen.getByLabelText("Folder name"), "Round 16{Enter}");

  await sidebar().findByRole("button", { name: "Round 16, 0 sessions" });
  expect(backend.requests).toContainEqual({
    method: "POST",
    url: "/api/folders/",
    body: { name: "Round 16" },
  });
  expect(screen.queryByLabelText("Folder name")).toBeNull();
});

test("escape leaves a new folder unmade", async () => {
  fakeBackend();
  const user = setup();
  render(<App />);

  await user.click(await sidebar().findByRole("button", { name: "New folder" }));
  await user.type(screen.getByLabelText("Folder name"), "Round 16{Escape}");

  expect(screen.queryByLabelText("Folder name")).toBeNull();
  expect(screen.queryByRole("button", { name: /Round 16/ })).toBeNull();
});

test("says why a folder can't be made, and keeps what was typed", async () => {
  const backend = fakeBackend();
  backend.folder("Round 3");
  const user = setup();
  render(<App />);

  await user.click(await sidebar().findByRole("button", { name: "New folder" }));
  await user.type(screen.getByLabelText("Folder name"), "round 3{Enter}");

  expect((await screen.findByRole("alert")).textContent).toContain(
    "There's already a folder called Round 3.",
  );
  expect((screen.getByLabelText("Folder name") as HTMLInputElement).value).toBe("round 3");
});

test("moves a session into a folder from its menu, and the folder opens to show it", async () => {
  const backend = fakeBackend();
  backend.folder("Round 2");
  const mug = backend.session("Mug ad");
  backend.session("Kettle ad");
  const user = setup();
  render(<App />);

  const row = (await sidebar().findByRole("button", { name: "Mug ad" })).closest("li")!;
  await user.click(within(row).getByRole("button", { name: "Move to folder" }));
  await user.click(screen.getByRole("menuitemradio", { name: "Round 2" }));

  await waitFor(() => expect(backend.folderOf(mug)).toBe("folder-1"));
  expect(looseSessions()).toEqual(["Kettle ad"]);
  expect(within(screen.getByRole("list", { name: "Round 2" })).getByText("Mug ad")).toBeTruthy();
  expect(screen.queryByRole("menu")).toBeNull();
});

test("takes a session out of its folder", async () => {
  const backend = fakeBackend();
  const round2 = backend.folder("Round 2");
  const mug = backend.session("Mug ad", round2);
  const user = setup();
  render(<App />);

  await user.click(await sidebar().findByRole("button", { name: "Round 2, 1 session" }));
  const row = within(screen.getByRole("list", { name: "Round 2" }))
    .getByRole("button", { name: "Mug ad" })
    .closest("li")!;
  await user.click(within(row).getByRole("button", { name: "Move to folder" }));
  await user.click(screen.getByRole("menuitem", { name: "Take out of folder" }));

  await waitFor(() => expect(looseSessions()).toEqual(["Mug ad"]));
  expect(backend.folderOf(mug)).toBeNull();
  expect(sidebar().getByRole("button", { name: "Round 2, 0 sessions" })).toBeTruthy();
});

test("the menu marks the folder a session is in, and offers no folders to leave when it is in none", async () => {
  const backend = fakeBackend();
  const round2 = backend.folder("Round 2");
  backend.folder("Round 3");
  backend.session("Mug ad", round2);
  backend.session("Kettle ad");
  const user = setup();
  render(<App />);

  const kettle = (await sidebar().findByRole("button", { name: "Kettle ad" })).closest("li")!;
  await user.click(within(kettle).getByRole("button", { name: "Move to folder" }));
  expect(screen.queryByRole("menuitem", { name: "Take out of folder" })).toBeNull();
  await user.keyboard("{Escape}");
  expect(screen.queryByRole("menu")).toBeNull();

  await user.click(sidebar().getByRole("button", { name: "Round 2, 1 session" }));
  const mug = within(screen.getByRole("list", { name: "Round 2" }))
    .getByRole("button", { name: "Mug ad" })
    .closest("li")!;
  await user.click(within(mug).getByRole("button", { name: "Move to folder" }));
  expect(screen.getByRole("menuitemradio", { name: "Round 2" }).getAttribute("aria-checked")).toBe(
    "true",
  );
  expect(screen.getByRole("menuitemradio", { name: "Round 3" }).getAttribute("aria-checked")).toBe(
    "false",
  );
});

test("makes a new folder from a session's menu and moves the session into it", async () => {
  const backend = fakeBackend();
  const mug = backend.session("Mug ad");
  const user = setup();
  render(<App />);

  const row = (await sidebar().findByRole("button", { name: "Mug ad" })).closest("li")!;
  await user.click(within(row).getByRole("button", { name: "Move to folder" }));
  await user.click(screen.getByRole("menuitem", { name: "New folder…" }));
  await user.type(screen.getByLabelText("Folder name"), "Round 16{Enter}");

  await waitFor(() => expect(backend.folderOf(mug)).toBe("folder-1"));
  expect(within(screen.getByRole("list", { name: "Round 16" })).getByText("Mug ad")).toBeTruthy();
});

test("drops a dragged session onto a folder to move it there", async () => {
  const backend = fakeBackend();
  backend.folder("Round 2");
  const mug = backend.session("Mug ad");
  render(<App />);

  const session = await sidebar().findByRole("button", { name: "Mug ad" });
  const folder = sidebar().getByRole("button", { name: "Round 2, 0 sessions" });
  fireEvent.dragStart(session);
  fireEvent.dragEnter(folder);
  fireEvent.dragOver(folder);
  expect(folder.closest("li")?.classList.contains("drop-target")).toBe(true);
  fireEvent.drop(folder);
  fireEvent.dragEnd(session);

  await waitFor(() => expect(backend.folderOf(mug)).toBe("folder-1"));
  expect(folder.closest("li")?.classList.contains("drop-target")).toBe(false);
});

test("drops a session from a folder onto the loose list to take it out", async () => {
  const backend = fakeBackend();
  const round2 = backend.folder("Round 2");
  const mug = backend.session("Mug ad", round2);
  backend.session("Kettle ad");
  const user = setup();
  render(<App />);

  await user.click(await sidebar().findByRole("button", { name: "Round 2, 1 session" }));
  const session = within(screen.getByRole("list", { name: "Round 2" })).getByRole("button", {
    name: "Mug ad",
  });
  const loose = screen.getByRole("region", { name: "Not in a folder" });
  fireEvent.dragStart(session);
  fireEvent.dragOver(loose);
  fireEvent.drop(loose);

  await waitFor(() => expect(backend.folderOf(mug)).toBeNull());
});

test("renames a folder", async () => {
  const backend = fakeBackend();
  backend.folder("Rnd 2");
  const user = setup();
  render(<App />);

  const row = (await sidebar().findByRole("button", { name: "Rnd 2, 0 sessions" })).closest("li")!;
  await user.click(within(row).getByRole("button", { name: "Folder options" }));
  await user.click(screen.getByRole("menuitem", { name: "Rename folder" }));
  const name = screen.getByLabelText("Folder name");
  expect((name as HTMLInputElement).value).toBe("Rnd 2");
  await user.clear(name);
  await user.type(name, "Round 2{Enter}");

  await sidebar().findByRole("button", { name: "Round 2, 0 sessions" });
  expect(backend.requests).toContainEqual({
    method: "PATCH",
    url: "/api/folders/folder-1/",
    body: { name: "Round 2" },
  });
});

test("removing a folder asks first, then keeps its sessions outside any folder", async () => {
  const backend = fakeBackend();
  const round2 = backend.folder("Round 2");
  backend.session("Mug ad", round2);
  const user = setup();
  render(<App />);

  const row = (await sidebar().findByRole("button", { name: "Round 2, 1 session" })).closest("li")!;
  await user.click(within(row).getByRole("button", { name: "Folder options" }));
  await user.click(screen.getByRole("menuitem", { name: "Remove folder" }));
  expect(row.textContent).toContain("Its 1 session stays");
  await user.click(within(row).getByRole("button", { name: "Cancel" }));
  expect(backend.requests.some((r) => r.method === "DELETE")).toBe(false);

  await user.click(within(row).getByRole("button", { name: "Folder options" }));
  await user.click(screen.getByRole("menuitem", { name: "Remove folder" }));
  await user.click(within(row).getByRole("button", { name: "Remove" }));

  await waitFor(() => expect(looseSessions()).toEqual(["Mug ad"]));
  expect(screen.queryByRole("button", { name: /Round 2/ })).toBeNull();
});
