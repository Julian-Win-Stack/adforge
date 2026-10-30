import { useEffect, useRef, type ClipboardEvent } from "react";
import { CloseIcon } from "./icons";

/** The photo formats the app takes: what the models can read. */
export const PHOTO_TYPES = ["image/jpeg", "image/png", "image/webp", "image/gif"];

/** The photos a paste carries, such as one copied from a web page. A paste of photos alone
 * doesn't also land in the box it was pasted into; one that carries text too pastes its
 * text as well. */
export function pastedPhotos(event: ClipboardEvent): File[] {
  const pictures = Array.from(event.clipboardData.files).filter((file) =>
    PHOTO_TYPES.includes(file.type),
  );
  if (pictures.length > 0 && !event.clipboardData.getData("text")) event.preventDefault();
  return pictures;
}

/** A photo waiting to be sent: one the owner added, or one they sent with a paste the app
 * couldn't use, which is on the server already. */
export type Photo = { name: string; file: File } | { name: string; url: string };

function preview(file: File) {
  try {
    return URL.createObjectURL(file);
  } catch {
    // Without a picture of it, the photo shows by its name.
    return null;
  }
}

/** A photo shown small while it waits to be sent, named `label` (its name by default). The
 * picture made of a file in memory is let go once it is taken away. */
export function Thumb({
  photo,
  label = photo.name,
  onRemove,
}: {
  photo: Photo;
  label?: string;
  onRemove: (() => void) | null;
}) {
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
    <figure className="photo-thumb">
      <img
        ref={picture}
        src={"url" in photo ? photo.url : undefined}
        alt={label}
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
