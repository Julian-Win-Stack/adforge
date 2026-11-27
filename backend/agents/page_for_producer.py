"""What the producer itself is given of a product page once it is read (#769, ADR 0016): the
page's text (its words, prices and sale, product options), the shop's words from its product
videos, and its product photos as real pictures, labelled by library number, so it can name
a photo by that number without a look."""

import io

import PIL.Image

from adforge import file_store
from chat.models import Message
from gateway.types import Image
from jobs import notices, page, work
from jobs.library import OwnerPhoto, PagePhoto
from jobs.library_items import library_line, originals
from jobs.models import Job
from jobs.notices import post_notice
from jobs.watcher import MUSIC_ONLY

from .loop import ToolResult

# The most product photos put in the producer's conversation. A page's gallery rarely holds
# more, and the newest answer that shows them gives them on every turn until it folds.
MOST_PHOTOS = 12
# How the page begins in a tool's answer, after what the tool itself says.
PAGE_HEADING = "What the page says (its words, prices, sale and product options):"


def with_the_page(job: Job, told: str) -> ToolResult:
    """A tool's answer `told`, followed by the job's page as the producer reads it and its
    product photos as pictures."""
    words, pictures = page_view(job)
    return ToolResult(f"{told}\n\n{words}", pictures)


def page_view(job: Job) -> tuple[str, list[Image]]:
    """The page's text and the shop's video words, as the producer reads them, then a line per
    product photo; and the photos as pictures, each labelled by its library number."""
    lines, pictures = _photos(job)
    said = [words for words in work._video_transcripts(job) if words != MUSIC_ONLY]
    parts = [
        PAGE_HEADING,
        page.for_model(job.page_text) or "(no text)",
    ]
    if said:
        parts += ["What the shop says in its product videos:", *said]
    if lines:
        parts += [
            "The product photos, shown to you as pictures labelled by library number. Name a "
            "photo by that number:",
            *lines,
        ]
    return "\n".join(parts), pictures


def _photos(job: Job) -> tuple[list[str], list[Image]]:
    """The job's product photos, the page's and the shop owner's (never a frame of a video or
    a version of a photo), each as its library line and as a picture. A photo whose file
    can't be loaded is left out, with a developer notice."""
    lines: list[str] = []
    pictures: list[Image] = []
    photos = [item for item in originals(job) if isinstance(item.source, (PagePhoto, OwnerPhoto))]
    if len(photos) > MOST_PHOTOS:
        post_notice(
            job,
            notices.PRODUCER_PHOTO_NOT_LOADED,
            f"Only the first {MOST_PHOTOS} of the {len(photos)} product photos are shown to the "
            "producer; it knows the rest by number only.",
            Message.Level.PROBLEM,
        )
    for item in photos[:MOST_PHOTOS]:
        try:
            with PIL.Image.open(io.BytesIO(file_store.read(item.file))) as opened:
                opened.load()
        except Exception as error:  # noqa: BLE001 (any file that can't be shown is told)
            post_notice(
                job,
                notices.PRODUCER_PHOTO_NOT_LOADED,
                f"Library item {item.number} couldn't be shown to the producer ({error}); it "
                "plans without seeing it.",
                Message.Level.PROBLEM,
            )
            continue
        lines.append(library_line(item))
        pictures.append(Image(label=f"Library item {item.number}", key=item.file))
    if len(photos) > MOST_PHOTOS:
        lines.append(
            f"The library has {len(photos) - MOST_PHOTOS} more product photos, not shown: "
            f"items {', '.join(str(item.number) for item in photos[MOST_PHOTOS:])}."
        )
    return lines, pictures
