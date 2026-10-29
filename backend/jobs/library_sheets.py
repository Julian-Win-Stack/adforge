"""What the planner, and a scene's rewrite, are shown of the product (spec #210 point 9): the
page videos' moments as text, and the library as numbered sheets, several pictures to an
image, each picture's library number and label under it, so the 50 pictures a model takes in
one call hold the whole library. A library bigger than the sheets can show has its last
pictures left off, and the chat is told how many.

The plan names pictures by their library numbers. Its needs and colour photos are kept as
the product photos those numbers are (`photo_positions`), which the starting picture is made
from until cut-and-send replaces it."""

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass

from django.conf import settings

from adforge import file_store
from chat.models import Message
from gateway.types import Image

from . import picker_grids
from .clip_judge import MOST_PICTURES
from .library import (
    LibraryEntry,
    LookFrame,
    OwnerPhoto,
    PagePhoto,
    ProductOnly,
    Recoloured,
    library_of,
    watch_of,
)
from .models import Job
from .notices import post_notice

# Each picture on a sheet is fitted in a square this many px a side.
SHEET_TILE = 600
# A look frame's product photo is the frame kept at its second: never further off than this.
MOST_SECONDS_OFF = 0.5


@dataclass(frozen=True)
class Sheets:
    """The library's sheets for one call: each sheet's picture and the numbers on it, how many
    pictures the library holds and how many of them no sheet shows."""

    images: list[Image]
    numbers: dict[Image, list[int]]
    library_pictures: int
    not_shown: int

    def numbers_on(self, images: Sequence[Image]) -> set[int]:
        """The library numbers on `images`, which are some of these sheets."""
        return {number for image in images for number in self.numbers[image]}


def per_sheet() -> int:
    """How many pictures one sheet holds."""
    across: int = settings.LIBRARY_SHEET_ACROSS
    return across * across


def most_shown() -> int:
    """The most library pictures the sheets show: as many as fill the pictures one model
    call takes."""
    return MOST_PICTURES * per_sheet()


def sheets_of(job: Job) -> Sheets:
    """`job`'s library on numbered sheets, its earliest pictures first, as many as the sheets
    can show. Each sheet is kept in the file store under a name made from what is on it, so
    the same library planned again makes none twice."""
    library = library_of(job)
    shown = library[: most_shown()]
    images: list[Image] = []
    numbers: dict[Image, list[int]] = {}
    for start in range(0, len(shown), per_sheet()):
        on_it = shown[start : start + per_sheet()]
        first, last = on_it[0].number, on_it[-1].number
        image = Image(
            label=(
                f"Library pictures {first} to {last} (each numbered and labelled under it)"
                if first != last
                else f"Library picture {first} (numbered and labelled under it)"
            ),
            key=_kept_sheet(job, on_it),
        )
        images.append(image)
        numbers[image] = [item.number for item in on_it]
    return Sheets(
        images=images,
        numbers=numbers,
        library_pictures=len(library),
        not_shown=len(library) - len(shown),
    )


def tile_text(item: LibraryEntry) -> str:
    """What is written under a library picture on its sheet: its number and label, and for a
    look frame the video and second it is from."""
    text = f"{item.number}. {item.label}"
    if isinstance(item.source, LookFrame):
        text += f" (from the product's video {item.source.video} at {item.source.second:g} s)"
    return text


def _kept_sheet(job: Job, items: list[LibraryEntry]) -> str:
    across: int = settings.LIBRARY_SHEET_ACROSS
    tiles = [(tile_text(item), item.file) for item in items]
    named = hashlib.sha256(json.dumps([across, SHEET_TILE, tiles]).encode()).hexdigest()[:24]
    return file_store.save_once(
        f"jobs/{job.pk}/sheets/{named}.jpg",
        lambda: picker_grids.grid(
            [(text, file_store.read(file)) for text, file in tiles],
            across,
            SHEET_TILE,
            label_under=True,
        ),
        whole=bool,
    )


def tell_not_shown(job: Job, sheets: Sheets) -> None:
    """Tell the chat in red, with its warning line, how many of the library's pictures no
    sheet shows, once for the same count."""
    if not sheets.not_shown:
        return
    first = sheets.library_pictures - sheets.not_shown + 1
    which = (
        f"picture {first} is"
        if sheets.not_shown == 1
        else f"pictures {first} to {sheets.library_pictures} are"
    )
    text = (
        f"The product's library has {sheets.library_pictures} pictures, more than the "
        f"{most_shown()} the planner can be shown, so {sheets.not_shown} of them are not "
        f"shown: {which} left out when the ad is planned or a scene is rewritten."
    )
    if all(warning["text"] != text for warning in job.warnings):
        post_notice(job, text, Message.Level.PROBLEM)


def moments_text(job: Job) -> str:
    """Every moment of `job`'s watched page videos, one a line, in the videos' order: its
    video, its number, its seconds, what happens and what is on screen. Empty when no video
    has any."""
    lines = []
    for video in job.videos.filter(watched__isnull=False).order_by("position"):
        watched = watch_of(video)
        for moment in watched.moments if watched else []:
            lines.append(
                f"Video {video.position}, moment {moment.number}, {moment.start:g}-"
                f"{moment.end:g} s: {moment.happens} On screen: {moment.on_screen}"
            )
    return "\n".join(lines)


def photo_positions(job: Job) -> dict[int, int]:
    """The product photo each library picture is, by library number, as the photo's
    position: a page photo's own, the photo the shop owner sent, the video picture kept for a
    look frame, and for a version of another picture that picture's. A picture with no product
    photo, such as an owner part's, isn't in it."""
    photos = list(job.photos.select_related("from_video"))
    by_number: dict[int, int] = {}
    for item in library_of(job):
        position: int | None = None
        match item.source:
            case PagePhoto(photo=photo):
                position = next((p.position for p in photos if p.position == photo), None)
            case OwnerPhoto(original=original):
                position = next((p.position for p in photos if p.file == original), None)
            case LookFrame(video=video, second=second):
                position = next(
                    (
                        p.position
                        for p in photos
                        if p.from_video is not None
                        and p.from_video.position == video
                        and p.at_second is not None
                        and abs(p.at_second - second) <= MOST_SECONDS_OFF
                    ),
                    None,
                )
            case ProductOnly(of=of) | Recoloured(of=of):
                position = by_number.get(of)
        if position is not None:
            by_number[item.number] = position
    return by_number
