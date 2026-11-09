"""Every view of the product its page shows, added by code beside the scene agent's picks
(round 13 root cause 1, Julian Nov 9 2026). The image model, the picture check and the clip
judge each get them, so a side the agent never picked (Hypervolt's back) is still seen, and
each photo is labelled with what the page photo shows, not only the agent's words.

A view is a page photo of the product on its own (library kind side, alone yes) that the
agent didn't pick and the owner didn't exclude for the scene. It goes in its own colour,
never recoloured: one in another colour than the picture's is labelled as showing the shape
only. The agent's own picks keep their recolour (draw_starting_picture.swapped)."""

from collections.abc import Iterable, Sequence

from adforge import file_store
from chat.models import Message
from jobs import cut_and_send, library_items, notices, scene_references
from jobs.library import LibraryEntry, PagePhoto, library_of
from jobs.models import Job, Scene

from .photos import MOST_EDIT_PHOTOS, SelectedPhoto

# The picture model takes 16 pictures in all: the picture being edited, if any, and photos.
MOST_PICTURES = MOST_EDIT_PHOTOS + 1

# How the picture check treats the views' colour (Julian Nov 9 2026, 20:57): shape and parts
# from them, colour only from the agent's photos. The clip judge's: clip_judge.JUDGE_COLOUR_RULE.
CHECK_COLOUR_RULE = (
    'Some photos are extra views of the product from its page, each marked "Extra view". '
    "Use them to learn the product's shape, parts and sides. Judge colour only against the "
    "photos the agent picked to draw the picture, never against an extra view."
)


def added(
    scene: Scene,
    picked: Iterable[int | None],
    *,
    room: int,
    first: int,
    making: str,
) -> list[SelectedPhoto]:
    """The views of `scene`'s product that code adds beside the library items `picked`, at
    most `room` of them, their lines numbered from Image `first` for the image model. A view
    whose file can't be found, or that doesn't fit the room, is left out with a developer
    notice naming what was being made (`making`)."""
    job = scene.job
    taken = {number for number in picked if number is not None}
    views = [
        item
        for item in library_items.originals(job)
        if isinstance(item.source, PagePhoto)
        and item.kind == "side"
        and item.alone == "yes"
        and item.number not in taken
    ]
    excluded = set(scene_references.excluded_among(scene, (item.number for item in views)))
    views = [item for item in views if item.number not in excluded]
    missing = [item for item in views if not file_store.exists(item.file)]
    if missing:
        _left_out(scene, missing, making, "their files could not be found")
    views = [item for item in views if item not in missing]
    if len(views) > room:
        _left_out(
            scene,
            views[max(room, 0) :],
            making,
            f"the picture model takes {MOST_PICTURES} pictures at most",
        )
        views = views[: max(room, 0)]
    colour = cut_and_send.planned_colour(scene)
    unsided = [item for item in views if item.side == "none"]
    if unsided:
        notices.post_notice(
            job,
            notices.PAGE_VIEW_UNLABELLED,
            f"Scene {scene.number}'s {making}: the page's {_views(unsided)} sent with no side, "
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


def check_lines(job: Job, photos: Sequence[SelectedPhoto], colour: str) -> list[str]:
    """The picture check's line for each photo: an agent's pick says what its page photo
    shows after the agent's own line, a view what it shows alone, and the shared room or
    person picture keeps its line. A pick the library no longer holds keeps only the agent's
    line, with a developer notice."""
    library = _library(job, photos)
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
            lines.append(
                f"{photo.line} (The page photo shows: {_label(item)}, side: {_side(item)}.)"
            )
    return lines


def judge_label(job: Job, photo: SelectedPhoto, colour: str) -> str:
    """What the clip judge is told of one drawing photo after its number: an agent's pick,
    what its page photo shows and what the agent asked for; a view, what it shows. A pick
    the library no longer holds keeps only the agent's line, with a developer notice."""
    item = _library(job, [photo]).get(photo.library_number)
    if item is None:
        return photo.line
    if photo.page_view:
        return f"{_label(item)} (side: {_side(item)})." + _other_colour(item, colour)
    return (
        f"The page photo shows: {_label(item)} (side: {_side(item)}). "
        f"What the agent asked for: {photo.line}"
    )


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


def _label(item: LibraryEntry) -> str:
    return item.label.strip().rstrip(".")


def _side(item: LibraryEntry) -> str:
    return "not given" if item.side == "none" else item.side


def _library(job: Job, photos: Iterable[SelectedPhoto]) -> dict[int | None, LibraryEntry]:
    """The library items of `photos`, by number; a number the library doesn't hold is told
    in a developer notice."""
    library: dict[int | None, LibraryEntry] = {item.number: item for item in library_of(job)}
    gone = sorted(
        {
            photo.library_number
            for photo in photos
            if photo.library_number is not None and photo.library_number not in library
        }
    )
    if gone:
        notices.post_notice(
            job,
            notices.PAGE_VIEW_UNLABELLED,
            f"Library items {', '.join(map(str, gone))} are no longer in the library, so the "
            "checks were told only the agent's words for them, not what the page photo shows.",
            Message.Level.PROBLEM,
        )
    return library


def _left_out(scene: Scene, items: list[LibraryEntry], making: str, why: str) -> None:
    notices.post_notice(
        scene.job,
        notices.PAGE_VIEW_LEFT_OUT,
        f"Scene {scene.number}'s {making}: the page's {_views(items)} left out, as {why}.",
        Message.Level.PROBLEM,
    )


def _views(items: list[LibraryEntry]) -> str:
    """ "view 2 was" or "views 2, 5 were"."""
    numbers = ", ".join(str(item.number) for item in items)
    return f"views {numbers} were" if len(items) > 1 else f"view {numbers} was"
