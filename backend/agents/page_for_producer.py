"""What the producer itself is given of a product page once it is read (#769): the page's text
(description, prices and sale, product options), the shop's words from its videos, and its
product photos as real pictures, numbered as in the library. It plans product-shots ads and
writes the plan card from these, so it needs no Look for them (ADR 0016)."""

import io

import PIL.Image

from adforge import file_store
from chat.models import Message
from gateway.types import Image
from jobs import notices, page
from jobs.library import OwnerPhoto, PagePhoto
from jobs.library_items import library_line, originals
from jobs.models import Job
from jobs.notices import post_notice

# The most product photos put in the producer's conversation: a page's own gallery rarely has
# more, and every picture is sent again on each of its turns until the call folds.
MOST_PHOTOS = 12


def photos_for_producer(job: Job) -> tuple[list[str], list[Image]]:
    """The job's product photos (page photos and the shop owner's, never frames cut from a
    video or versions of a photo), each as its library line and as a picture labelled by its
    library number. A photo whose file can't be read is left out with a developer notice."""
    lines: list[str] = []
    pictures: list[Image] = []
    for item in originals(job):
        if not isinstance(item.source, (PagePhoto, OwnerPhoto)):
            continue
        if len(pictures) >= MOST_PHOTOS:
            break
        try:
            with PIL.Image.open(io.BytesIO(file_store.read(item.file))) as opened:
                opened.verify()
        except Exception as error:  # noqa: BLE001 (any unreadable file is left out, told)
            post_notice(
                job,
                notices.PRODUCER_PHOTO_NOT_SHOWN,
                f"Library item {item.number} couldn't be shown to the producer ({error}); it "
                "plans without seeing it.",
                Message.Level.PROBLEM,
            )
            continue
        lines.append(library_line(item))
        pictures.append(Image(label=f"Library item {item.number}", key=item.file))
    return lines, pictures


def page_for_producer(job: Job) -> tuple[str, list[Image]]:
    """The page's text and the shop's video words, as the producer reads them, and its
    product photos as pictures."""
    lines, pictures = photos_for_producer(job)
    said = list(job.videos.exclude(transcript="").values_list("transcript", flat=True))
    parts = [
        "What the page says (its text, prices, sale and product options):",
        page.for_model(job.page_text) or "(no text)",
    ]
    if said:
        parts += ["What the shop says in its product videos:", *said]
    if lines:
        parts += [
            "The product photos, shown to you as pictures labelled by their library number. "
            "Name a photo by that number:",
            *lines,
        ]
    return "\n".join(parts), pictures
