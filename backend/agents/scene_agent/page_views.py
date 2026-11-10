"""Every view of the product its page shows, added by code beside the scene agent's picks
(spec #514 root cause 1). The image model and the picture check get them, so a side the
agent never picked is still seen, and each photo is labelled from the page, not only with
the agent's words.

A view is an original page photo of the product on its own (library kind side, alone yes)
that the agent didn't pick and the owner didn't exclude for the scene, in any colour."""

import re
from collections.abc import Iterable, Sequence

from adforge import file_store
from chat.models import Message
from jobs import cut_and_send, library_items, notices, scene_references
from jobs.library import LibraryEntry, PagePhoto, Recoloured, library_of
from jobs.models import Job, ProducedItem, Scene

from .photos import MOST_EDIT_PHOTOS, SelectedPhoto

# The image model takes 16 pictures in all: the picture being edited, if any, and photos.
MOST_PICTURES = MOST_EDIT_PHOTOS + 1

# The image model's number an agent's line starts with ("Image 2: "), not the judge's.
_AGENTS_NUMBER = re.compile(r"^Image \d+:\s*")

# How the picture check treats the views' colour (spec #514): shape, parts and sides from
# them; colour only from the agent's photos. Told only when views are sent.
CHECK_COLOUR_RULE = (
    'Some photos are extra views of the product from its page, each marked "Extra view". '
    "Use them to learn the product's shape, parts and sides. Judge colour only against the "
    "photos the agent picked to draw the picture, never against an extra view."
)


def added(
    scene: Scene, picked: Iterable[int | None], *, room: int, first: int, making: str
) -> list[SelectedPhoto]:
    """The views of `scene`'s product code adds beside the library items `picked`, at most
    `room` of them, their lines numbered from Image `first` for the image model. A view whose
    file can't be found, or that doesn't fit the room, is left out, and a view with no side
    is sent without one, each with a developer notice naming what was being made
    (`making`: starting picture, edited picture, end picture)."""
    job = scene.job
    originals = library_items.originals(job)
    taken = {number for number in picked if number is not None}
    views = [
        item
        for item in originals
        if isinstance(item.source, PagePhoto)
        and item.kind == "side"
        and item.alone == "yes"
        and item.number not in taken
    ]
    excluded = set(scene_references.excluded_among(scene, (item.number for item in views)))
    views = [item for item in views if item.number not in excluded]
    missing = [item for item in views if not file_store.exists(item.file)]
    if missing:
        _left_out(
            scene,
            making,
            missing,
            "their files could not be found" if len(missing) > 1 else "its file could not be found",
        )
    colour = cut_and_send.planned_colour(scene)
    views = in_kept_order(
        [item for item in views if item not in missing],
        shown={item.side for item in originals if item.number in taken},
        colour=colour,
    )
    room = max(room, 0)
    if len(views) > room:
        _left_out(
            scene, making, views[room:], f"the image model takes {MOST_PICTURES} pictures at most"
        )
        views = views[:room]
    sideless = [item for item in views if item.side == "none"]
    if sideless:
        notices.post_notice(
            job,
            notices.PAGE_VIEW_UNLABELLED,
            f"Scene {scene.number}'s {making}: the page's {_views(sideless)} sent with no side, "
            "as the library gives none.",
            Message.Level.PROBLEM,
        )
    return [
        SelectedPhoto(
            file=item.file,
            line=f"Image {number}: {_drawing_line(item, colour)}",
            library_number=item.number,
            original_file=item.file,
            page_view=True,
        )
        for number, item in enumerate(views, first)
    ]


def in_kept_order(
    views: Sequence[LibraryEntry], *, shown: set[str], colour: str
) -> list[LibraryEntry]:
    """`views` in the order they are kept when the image model's room runs short (Julian, Nov
    10 2026): one in the picture's `colour` for each side the picks don't show (`shown`),
    then one in any colour for each side still not shown, then the rest, each part in
    library order. A view with no side shows no side, so it is among the rest."""
    kept: list[LibraryEntry] = []
    for same_colour_only in (True, False):
        for item in views:
            if item.side == "none" or item.side in shown:
                continue
            if not same_colour_only or not _other_colour(item, colour):
                kept.append(item)
                shown = shown | {item.side}
    return kept + [item for item in views if item not in kept]


def check_lines(
    job: Job, photos: Sequence[SelectedPhoto], colour: str, *, scene: int, making: str
) -> list[str]:
    """The picture check's line for each of `photos`, for a picture in `colour`: a view says
    what its page photo shows; an agent's pick adds it after the agent's own line; the room
    or person picture keeps its line. A pick the library no longer holds keeps only the
    agent's line, with a developer notice naming the scene and what was being made."""
    library, recoloured = _library(job)
    _post_gone(job, photos, library, scene=scene, making=making, told="the picture check")
    lines: list[str] = []
    for photo in photos:
        item = library.get(photo.library_number) if photo.library_number is not None else None
        if item is None:
            lines.append(photo.line)
        elif photo.page_view:
            lines.append(
                f"Extra view, page photo {item.number}: {_label(item)} (side: {_side(item)})."
                + _other_colour(item, colour)
            )
        else:
            sent = recoloured.get(photo.file)
            lines.append(
                f"{photo.line} (The page photo shows: {_label(item)}, side: {_side(item)}"
                + (f"; sent recoloured to {sent.colour}" if sent is not None else "")
                + ".)"
            )
    return lines


def judge_labels(
    job: Job, photos: Sequence[SelectedPhoto], colour: str, *, scene: int
) -> list[str]:
    """What the clip judge is told of each of `photos`, the drawing photos of a clip in
    `colour`: a view, what its page photo shows; an agent's pick, what its page photo shows,
    then what the agent asked for. A pick the library no longer holds keeps only the agent's
    words, with a developer notice naming the scene."""
    library, recoloured = _library(job)
    _post_gone(job, photos, library, scene=scene, making="clip", told="the clip judge")
    labels: list[str] = []
    for photo in photos:
        asked = _AGENTS_NUMBER.sub("", photo.line, count=1)
        item = library.get(photo.library_number) if photo.library_number is not None else None
        if item is None:
            labels.append(f"What the agent asked for: {asked}")
        elif photo.page_view:
            labels.append(f"{_label(item)} (side: {_side(item)})." + _other_colour(item, colour))
        else:
            sent = recoloured.get(photo.file)
            labels.append(
                f"The page photo shows: {_label(item)} (side: {_side(item)})"
                + (f"; sent recoloured to {sent.colour}" if sent is not None else "")
                + f". What the agent asked for: {asked}"
            )
    return labels


def drawn_with(picture: ProducedItem) -> list[SelectedPhoto]:
    """The page views `picture` was drawn and checked with, as its sent record keeps them;
    whatever views they were, so the clip judge gets the same set."""
    return [
        photo
        for photo in map(SelectedPhoto.model_validate, (picture.sent or {}).get("photos", []))
        if photo.page_view
    ]


def has_views(photos: Iterable[SelectedPhoto]) -> bool:
    return any(photo.page_view for photo in photos)


def _drawing_line(item: LibraryEntry, colour: str) -> str:
    """The image model's line for a view: what it shows, and to take its shape, parts and
    sides, never its colour."""
    return (
        f"an extra view of the product from its page: {_label(item)} (side: {_side(item)}). "
        "Take only its shape, parts and sides from it, never its colour."
        + _other_colour(item, colour)
    )


def _other_colour(item: LibraryEntry, colour: str) -> str:
    if item.colour in (colour, "none"):
        return ""
    return (
        f" This photo shows the product in {item.colour}; it shows the shape only. The ad's "
        f"product is {colour}."
    )


def _library(job: Job) -> tuple[dict[int, LibraryEntry], dict[str, LibraryEntry]]:
    """`job`'s library by number, and its recolours by file: a pick went recoloured to the
    picture's colour when its file is a recolour's."""
    entries = library_of(job)
    return (
        {item.number: item for item in entries},
        {item.file: item for item in entries if isinstance(item.source, Recoloured)},
    )


def _post_gone(
    job: Job,
    photos: Sequence[SelectedPhoto],
    library: dict[int, LibraryEntry],
    *,
    scene: int,
    making: str,
    told: str,
) -> None:
    """A developer notice when any of `photos` is a library item `library` no longer holds,
    so `told` (the picture check, the clip judge) gets only the agent's words for it."""
    gone = sorted(
        {
            photo.library_number
            for photo in photos
            if photo.library_number is not None and photo.library_number not in library
        }
    )
    if not gone:
        return
    numbers = ", ".join(map(str, gone))
    notices.post_notice(
        job,
        notices.PAGE_VIEW_UNLABELLED,
        f"Scene {scene}'s {making}: "
        + (
            f"library items {numbers} are no longer in the library, so {told} was told only "
            "the agent's words for them"
            if len(gone) > 1
            else f"library item {numbers} is no longer in the library, so {told} was told "
            "only the agent's words for it"
        )
        + ", not what the page photo shows.",
        Message.Level.PROBLEM,
    )


def _label(item: LibraryEntry) -> str:
    return item.label.strip().rstrip(".")


def _side(item: LibraryEntry) -> str:
    return "not given" if item.side == "none" else item.side


def _left_out(scene: Scene, making: str, items: Sequence[LibraryEntry], why: str) -> None:
    notices.post_notice(
        scene.job,
        notices.PAGE_VIEW_LEFT_OUT,
        f"Scene {scene.number}'s {making}: the page's {_views(items, 'left out')}, as {why}.",
        Message.Level.PROBLEM,
    )


def _views(items: Sequence[LibraryEntry], done: str = "") -> str:
    """ "view 2 was" or "views 2, 5 were", in number order, then `done`."""
    numbers = ", ".join(str(number) for number in sorted(item.number for item in items))
    said = f"views {numbers} were" if len(items) > 1 else f"view {numbers} was"
    return f"{said} {done}" if done else said
