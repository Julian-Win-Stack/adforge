"""What the producer itself is given of a product page once it is read (#769, ADR 0016): the
page's text (its words, prices and sale, product options), the shop's words from its product
videos, and its product photos as real pictures, labelled by library number, so it can name
a photo by that number without a look."""

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
from jobs.watcher import MUSIC_ONLY

from .loop import ToolResult

# The most product photos put in the producer's conversation. A page's gallery rarely holds
# more, and each picture is given again on every turn until the call folds.
MOST_PHOTOS = 12
# How the page begins in a tool's answer, after what the tool itself says.
PAGE_HEADING = "What the page says (its words, prices, sale and product options):"


def with_the_page(job: Job, told: str) -> ToolResult:
    """A tool's answer `told`, followed by the job's page as the producer reads it and its
    product photos as pictures."""
    words, pictures = page_for_producer(job)
    return ToolResult(f"{told}\n\n{words}", pictures)


def page_for_producer(job: Job) -> tuple[str, list[Image]]:
    """The page's text and the shop's video words, as the producer reads them, then a line per
    product photo; and the photos as pictures, each labelled by its library number."""
    lines, pictures = _photos(job)
    said = list(
        job.videos.exclude(transcript="")
        .exclude(transcript=MUSIC_ONLY)
        .values_list("transcript", flat=True)
    )
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
    for item in photos[:MOST_PHOTOS]:
        try:
            with PIL.Image.open(io.BytesIO(file_store.read(item.file))) as opened:
                opened.verify()
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
    return lines, pictures
