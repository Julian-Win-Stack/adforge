import {
  useEffect,
  useRef,
  useState,
  type ClipboardEvent,
  type DragEvent,
  type KeyboardEvent,
} from "react";
import { ApiError } from "./api";
import { ChevronIcon, CloseIcon, ImageIcon } from "./icons";
import type { PagePanelState } from "./pagePanel";

const PHOTO_TYPES = ["image/jpeg", "image/png", "image/webp", "image/gif"];

function preview(file: File) {
  try {
    return URL.createObjectURL(file);
  } catch {
    // Without a picture of it, the photo shows by its name.
    return null;
  }
}

/** A photo the owner added, shown small while it waits to be sent. Its picture is let go
 * once it leaves the panel. */
function Thumb({ file, onRemove }: { file: File; onRemove: (() => void) | null }) {
  const picture = useRef<HTMLImageElement>(null);
  useEffect(() => {
    const made = preview(file);
    if (made && picture.current) picture.current.src = made;
    return () => {
      if (made) URL.revokeObjectURL(made);
    };
  }, [file]);
  return (
    <figure className="page-panel-thumb">
      <img ref={picture} alt={file.name} title={file.name} />
      {onRemove && (
        <button type="button" aria-label={`Remove ${file.name}`} onClick={onRemove}>
          <CloseIcon />
        </button>
      )}
    </figure>
  );
}

function words(text: string) {
  const count = text.split(/\s+/).filter(Boolean).length;
  return `${count.toLocaleString("en-US")} word${count === 1 ? "" : "s"}`;
}

/** Where the shop owner pastes the product page's text and adds its photos when the app
 * can't read the page from the link. Done sends both as one pasted page, which code checks
 * and keeps as the page. What was pasted stays here until the app accepts it, so a paste it
 * couldn't use can be fixed and sent again. The chat stays usable below; the panel folds
 * away to give it room. */
export function PagePanel({
  state,
  onDone,
}: {
  state: PagePanelState;
  onDone: (text: string, photos: File[]) => Promise<void>;
}) {
  const [text, setText] = useState("");
  const [photos, setPhotos] = useState<File[]>([]);
  const [open, setOpen] = useState(true);
  const [sending, setSending] = useState(false);
  const [refused, setRefused] = useState<string | null>(null);
  const [skipped, setSkipped] = useState(false);
  const [dragging, setDragging] = useState(false);
  const picker = useRef<HTMLInputElement>(null);

  const busy = sending || state.checking;
  const ready = text.trim().length > 0 && photos.length > 0;
  const error = busy ? null : (refused ?? state.reason);

  function add(files: Iterable<File>) {
    const all = Array.from(files);
    const usable = all.filter((file) => PHOTO_TYPES.includes(file.type));
    setSkipped(usable.length < all.length);
    if (usable.length === 0) return;
    setPhotos((have) => [...have, ...usable]);
  }

  function remove(index: number) {
    setPhotos((have) => have.filter((_, i) => i !== index));
    if (picker.current) picker.current.value = "";
  }

  async function done() {
    if (!ready || busy) return;
    setSending(true);
    setRefused(null);
    try {
      await onDone(text, photos);
    } catch (e) {
      setRefused(e instanceof ApiError ? e.message : "Couldn't reach the server. Try again.");
    } finally {
      setSending(false);
    }
  }

  function onDrop(event: DragEvent) {
    event.preventDefault();
    setDragging(false);
    add(event.dataTransfer.files);
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
    if (pictures.length === 0) return;
    if (!event.clipboardData.getData("text")) event.preventDefault();
    add(pictures);
  }

  return (
    <section
      className={`page-panel${open ? "" : " folded"}`}
      aria-label="Paste your product page"
      onPaste={onPaste}
    >
      {!open ? (
        <button type="button" className="page-panel-unfold" onClick={() => setOpen(true)}>
          <span className="page-panel-dot" aria-hidden />
          Paste your page
          <span className="page-panel-unfold-hint">
            {state.checking ? "Checking your page…" : "We couldn't read it from the link"}
          </span>
          <span className="page-panel-chevron up">
            <ChevronIcon />
          </span>
        </button>
      ) : (
        <>
          <header className="page-panel-head">
            <div>
              <p className="page-panel-title">Paste your product page</p>
              <p className="page-panel-sub">
                We couldn't read it from the link. Copy everything on your product page, paste it
                here and add the product's photos. A working link in the chat works too.
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

          <div className="page-panel-text">
            <textarea
              aria-label="Page text"
              placeholder="Paste the product page's text here"
              value={text}
              disabled={busy}
              onChange={(event) => setText(event.target.value)}
            />
            {text.trim() && <span className="page-panel-count">{words(text)}</span>}
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
          <div className="page-panel-photos">
            {photos.map((file, index) => (
              <Thumb
                key={`${file.name}-${index}`}
                file={file}
                onRemove={busy ? null : () => remove(index)}
              />
            ))}
            <div
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
              onDrop={(event) => (busy ? event.preventDefault() : onDrop(event))}
            >
              <ImageIcon size={photos.length ? 18 : 22} />
              {photos.length === 0 ? (
                <span>
                  <strong>Drop photos here</strong> or click to choose
                </span>
              ) : (
                <span>Add more</span>
              )}
            </div>
          </div>
          {skipped && (
            <p className="page-panel-skipped">Only photos can be added: JPG, PNG, WebP or GIF.</p>
          )}

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
              ) : ready ? (
                `${photos.length} photo${photos.length === 1 ? "" : "s"} ready`
              ) : (
                "Paste the page text and add at least one photo."
              )}
            </span>
            <button type="button" className="yes-button" disabled={!ready || busy} onClick={done}>
              Done
            </button>
          </div>
        </>
      )}
    </section>
  );
}
