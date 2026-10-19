import {
  useEffect,
  useRef,
  useState,
  type ClipboardEvent,
  type DragEvent,
  type KeyboardEvent,
} from "react";
import { ApiError, type Message } from "./api";
import { ChevronIcon, CloseIcon, ImageIcon } from "./icons";
import type { PagePanelState } from "./pagePanel";

const PHOTO_TYPES = ["image/jpeg", "image/png", "image/webp", "image/gif"];

/** A photo in the panel: one the owner added, or one they sent with a paste the app couldn't
 * use, which is on the server already. */
type Photo = { name: string; file: File } | { name: string; url: string };

function preview(file: File) {
  try {
    return URL.createObjectURL(file);
  } catch {
    // Without a picture of it, the photo shows by its name.
    return null;
  }
}

/** A photo shown small while it waits to be sent. The picture made of a file in memory is
 * let go once it leaves the panel. */
function Thumb({ photo, onRemove }: { photo: Photo; onRemove: (() => void) | null }) {
  const picture = useRef<HTMLImageElement>(null);
  const file = "file" in photo ? photo.file : null;
  useEffect(() => {
    if (file === null) return;
    const made = preview(file);
    if (made && picture.current) picture.current.src = made;
    return () => {
      if (made) URL.revokeObjectURL(made);
    };
  }, [file]);
  return (
    <figure className="page-panel-thumb">
      <img
        ref={picture}
        src={"url" in photo ? photo.url : undefined}
        alt={photo.name}
        title={photo.name}
      />
      {onRemove && (
        <button type="button" aria-label={`Remove ${photo.name}`} onClick={onRemove}>
          <CloseIcon />
        </button>
      )}
    </figure>
  );
}

/** A stored photo's name as the owner gave it: the server's file name, without the unique
 * id the server puts in front. */
function shownName(url: string) {
  const name = decodeURIComponent(url.split("/").pop() ?? url);
  return name.replace(/^[0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12}[-_]/i, "");
}

/** The file a photo is sent as: a photo already on the server is fetched back. */
async function asFile(photo: Photo): Promise<File> {
  if ("file" in photo) return photo.file;
  const response = await fetch(photo.url);
  if (!response.ok) throw new Error(`The server answered ${response.status}.`);
  const type = response.headers.get("Content-Type") ?? "";
  return new File([await response.arrayBuffer()], photo.name, { type });
}

export function wordCount(text: string) {
  const count = text.split(/\s+/).filter(Boolean).length;
  return `${count.toLocaleString("en-US")} word${count === 1 ? "" : "s"}`;
}

/** The page text being pasted is kept in the browser until it is sent, so a reload or a
 * closed tab doesn't lose a long paste. */
const draftKey = (sessionId: string) => `page-panel-draft:${sessionId}`;

function readDraft(sessionId: string): string | null {
  try {
    return localStorage.getItem(draftKey(sessionId));
  } catch {
    return null;
  }
}

function keepDraft(sessionId: string, text: string | null) {
  try {
    if (text) localStorage.setItem(draftKey(sessionId), text);
    else localStorage.removeItem(draftKey(sessionId));
  } catch {
    // Without storage the draft lives only as long as the page.
  }
}

function hint(text: string, photos: number) {
  if (!text.trim() && photos === 0) return "Paste the page text and add at least one photo.";
  if (!text.trim()) return "Paste the page text.";
  if (photos === 0) return "Add at least one photo.";
  return `${photos} photo${photos === 1 ? "" : "s"} ready`;
}

/** Where the shop owner pastes the product page's text and adds its photos when the app
 * can't read the page from the link. Done sends both as one pasted page, which code checks
 * and keeps as the page. What was pasted stays here until the app accepts it, so a paste it
 * couldn't use can be fixed and sent again. The chat stays usable below; the panel folds
 * away to give it room. */
export function PagePanel({
  sessionId,
  state,
  lastPasted,
  onDone,
}: {
  sessionId: string;
  state: PagePanelState;
  /** The owner's newest pasted page the app hasn't accepted, put back in the panel until
   * something is changed here, as after a reload. */
  lastPasted: Message | null;
  onDone: (text: string, photos: File[]) => Promise<void>;
}) {
  // Null until the owner changes them here: then they show the draft, or the paste the app
  // couldn't use.
  const [typed, setTyped] = useState<string | null>(() => readDraft(sessionId));
  const [added, setAdded] = useState<Photo[] | null>(null);
  const [open, setOpen] = useState(true);
  const [sending, setSending] = useState(false);
  const [refused, setRefused] = useState<string | null>(null);
  const [dismissed, setDismissed] = useState<string | null>(null);
  const [skipped, setSkipped] = useState(false);
  const [dragging, setDragging] = useState(false);
  const picker = useRef<HTMLInputElement>(null);
  const zone = useRef<HTMLDivElement>(null);
  const box = useRef<HTMLTextAreaElement>(null);
  const unfolded = useRef(false);

  const text = typed ?? lastPasted?.text ?? "";
  const photos: Photo[] =
    added ??
    (lastPasted
      ? lastPasted.attachments
          .filter((attachment) => attachment.kind === "picture")
          .map(({ url }) => ({ name: shownName(url), url }))
      : []);

  const busy = sending || state.checking;
  const ready = text.trim().length > 0 && photos.length > 0;
  const shownError = refused ?? state.reason;
  const error = busy || shownError === dismissed ? null : shownError;

  useEffect(() => {
    if (open && unfolded.current) box.current?.focus();
  }, [open]);

  /** A change to the paste or photos: the reason shown is for what was there before. */
  function changed() {
    setDismissed(shownError);
    setRefused(null);
  }

  function write(next: string) {
    setTyped(next);
    keepDraft(sessionId, next);
    changed();
  }

  function add(files: Iterable<File>) {
    const all = Array.from(files);
    const usable = all.filter((file) => PHOTO_TYPES.includes(file.type));
    setSkipped(usable.length < all.length);
    const fresh: File[] = [];
    for (const file of usable) {
      const same = (name: string, size: number) => name === file.name && size === file.size;
      if (photos.some((have) => "file" in have && same(have.name, have.file.size))) continue;
      if (fresh.some((have) => same(have.name, have.size))) continue;
      fresh.push(file);
    }
    if (fresh.length === 0) return;
    setAdded([...photos, ...fresh.map((file) => ({ name: file.name, file }))]);
    changed();
  }

  function remove(index: number) {
    setAdded(photos.filter((_, i) => i !== index));
    if (picker.current) picker.current.value = "";
    zone.current?.focus();
    changed();
  }

  async function done() {
    if (!ready || busy) return;
    setSending(true);
    setRefused(null);
    setDismissed(null);
    try {
      await onDone(text, await Promise.all(photos.map(asFile)));
      keepDraft(sessionId, null);
    } catch (e) {
      setRefused(e instanceof ApiError ? e.message : "Couldn't reach the server. Try again.");
    } finally {
      setSending(false);
    }
  }

  function onDrop(event: DragEvent) {
    event.preventDefault();
    setDragging(false);
    if (!busy) add(event.dataTransfer.files);
  }

  function onZoneKey(event: KeyboardEvent) {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      picker.current?.click();
    }
  }

  // A photo copied from the page can be pasted straight in, anywhere in the panel.
  function onPaste(event: ClipboardEvent) {
    const pictures = Array.from(event.clipboardData.files).filter((file) =>
      PHOTO_TYPES.includes(file.type),
    );
    if (pictures.length === 0 || busy) return;
    if (!event.clipboardData.getData("text")) event.preventDefault();
    add(pictures);
  }

  if (!open) {
    return (
      <section className="page-panel folded" aria-label="Paste your product page">
        <button
          type="button"
          className="page-panel-unfold"
          onClick={() => {
            unfolded.current = true;
            setOpen(true);
          }}
        >
          <span className="page-panel-dot" aria-hidden />
          Paste your page
          <span className="page-panel-unfold-hint">
            {state.checking
              ? "Checking your page…"
              : error
                ? error
                : text.trim()
                  ? `${wordCount(text)}, ${photos.length} photo${photos.length === 1 ? "" : "s"}`
                  : "We couldn't read it from the link"}
          </span>
          <span className="page-panel-chevron up">
            <ChevronIcon />
          </span>
        </button>
      </section>
    );
  }

  return (
    <section className="page-panel" aria-label="Paste your product page" onPaste={onPaste}>
      <header className="page-panel-head">
        <div>
          <p className="page-panel-title">Paste your product page</p>
          <p className="page-panel-sub">
            We couldn't read it from the link. Paste the page's text and add its photos
            <span className="page-panel-sub-more">, or send a working link below</span>.
          </p>
        </div>
        <button
          type="button"
          className="icon-button page-panel-fold"
          aria-label="Fold away"
          title="Fold away"
          onClick={() => setOpen(false)}
        >
          <ChevronIcon />
        </button>
      </header>

      <div className="page-panel-body">
        <div className="page-panel-text">
          <textarea
            ref={box}
            aria-label="Page text"
            placeholder="Paste the product page's text here: name, price, description, details…"
            value={text}
            disabled={busy}
            onChange={(event) => write(event.target.value)}
          />
          {text.trim() && <span className="page-panel-count">{wordCount(text)}</span>}
        </div>

        <input
          ref={picker}
          className="visually-hidden"
          aria-label="Product photos"
          type="file"
          multiple
          accept={PHOTO_TYPES.join(",")}
          tabIndex={-1}
          onChange={(event) => add(event.target.files ?? [])}
        />
        <div className={`page-panel-photos${photos.length ? " has-photos" : ""}`}>
          {photos.map((photo, index) => (
            <Thumb
              key={"file" in photo ? `${photo.name}-${photo.file.size}-${index}` : photo.url}
              photo={photo}
              onRemove={busy ? null : () => remove(index)}
            />
          ))}
          <div
            ref={zone}
            role="button"
            tabIndex={busy ? -1 : 0}
            aria-disabled={busy}
            aria-label="Add product photos"
            className={`page-panel-drop${dragging ? " over" : ""}${photos.length ? " small" : ""}`}
            onClick={() => !busy && picker.current?.click()}
            onKeyDown={(event) => !busy && onZoneKey(event)}
            onDragOver={(event) => {
              event.preventDefault();
              setDragging(true);
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={onDrop}
          >
            <ImageIcon size={photos.length ? 18 : 22} />
            {photos.length === 0 ? (
              <span>
                <strong>Add the product's photos</strong>
                <span className="page-panel-drop-how pointer">
                  Drop them here or click to choose
                </span>
                <span className="page-panel-drop-how touch">Tap to choose</span>
              </span>
            ) : (
              <span>Add</span>
            )}
          </div>
        </div>
        {skipped && (
          <p className="page-panel-skipped">Only photos can be added: JPG, PNG, WebP or GIF.</p>
        )}
      </div>

      {error && (
        <p role="alert" className="notice error page-panel-error">
          {error}
        </p>
      )}

      <div className="page-panel-foot">
        <span className="page-panel-hint" aria-live="polite">
          {busy ? (
            <span className="page-panel-checking">
              <span className="spinner" aria-hidden />
              Checking your page…
            </span>
          ) : (
            hint(text, photos.length)
          )}
        </span>
        <button type="button" className="yes-button" disabled={!ready || busy} onClick={done}>
          Done
        </button>
      </div>
    </section>
  );
}
