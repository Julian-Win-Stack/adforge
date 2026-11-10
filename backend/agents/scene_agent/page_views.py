"""The views of the product its page shows, added by code beside the scene agent's picks
(spec #514 root cause 1). The image model and the picture check get them, so a side the
agent never picked is still seen, and each photo is labelled from the page, not only with
the agent's words.

A view is, for each side the side picker found (jobs.side_photos, #525), its chosen photo,
sent as its true crop: the side's first ranked photo the owner didn't exclude for the scene
whose crop is usable. A side whose chosen photo the agent picked gets none. When the picker
failed for the ad, the views are every original page photo of the product on its own
(library kind side, alone yes) that the agent didn't pick and the owner didn't exclude,
in any colour (#516)."""

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Protocol

from adforge import file_store
from chat.models import Message
from jobs import cut_and_send, library_items, notices, scene_references, side_photos
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


@dataclass(frozen=True)
class View:
    """A page photo code adds: `file` is what is sent (its true crop, or the photo whole);
    `side` and `shows` are the picker's, or the library's side and None for the fallback."""

    item: LibraryEntry
    file: str
    side: str
    shows: str | None = None

    @property
    def number(self) -> int:
        return self.item.number

    @property
    def colour(self) -> str:
        return self.item.colour


class _Sided(Protocol):
    @property
    def side(self) -> str: ...

    @property
    def colour(self) -> str: ...


def added(
    scene: Scene, picked: Iterable[int | None], *, room: int, first: int, making: str
) -> list[SelectedPhoto]:
    """The views of `scene`'s product code adds beside the library items `picked`, at most
    `room` of them, their lines numbered from Image `first` for the image model. A view that
    doesn't fit the room is left out, a side whose ranked photos can't be sent goes without,
    and a fallback view whose file can't be found is left out or with no side is sent without
    one, each with a developer notice naming what was being made (`making`: starting
    picture, edited picture, end picture)."""
    job = scene.job
    taken = {number for number in picked if number is not None}
    answer = side_photos.answer_of(job)
    if answer is None:
        notices.post_once(
            job,
            notices.PAGE_VIEWS_EVERY_ALONE,
            "The side picker failed for this ad, so its scene pictures get every page photo of "
            "the product alone as extra views, not the picker's one photo per side.",
        )
        views, shown = _every_alone_view(scene, taken, making)
    else:
        views, shown = _chosen(scene, answer, taken, making)
    colour = cut_and_send.planned_colour(scene)
    views = in_kept_order(views, shown=shown, colour=colour)
    room = max(room, 0)
    if len(views) > room:
        _left_out(
            scene, making, views[room:], f"the image model takes {MOST_PICTURES} pictures at most"
        )
        views = views[:room]
    sideless = [view for view in views if view.side == "none"]
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
            file=view.file,
            line=f"Image {number}: {_drawing_line(view, colour)}",
            library_number=view.number,
            original_file=view.item.file,
            page_view=True,
            side=view.side if view.shows is not None else None,
            shows=view.shows,
        )
        for number, view in enumerate(views, first)
    ]


def _chosen(
    scene: Scene, answer: side_photos.Answer, taken: set[int], making: str
) -> tuple[list[View], set[str]]:
    """Each side's chosen photo the picks leave out, and the sides the picks show (a side
    one of whose ranked photos the agent picked). A side's first ranked photos that can't go
    are passed over for the next, said for the developer."""
    job = scene.job
    photos = {
        item.number: item
        for item in library_items.originals(job)
        if isinstance(item.source, PagePhoto)
    }
    ranked = {each.photo for side in answer.sides for each in side.ranked}
    excluded = set(scene_references.excluded_among(scene, (n for n in ranked if n in photos)))
    views: list[View] = []
    shown: set[str] = set()
    for side in answer.sides:
        if any(each.photo in taken for each in side.ranked):
            shown.add(side.side)
        passed: list[str] = []
        chosen: int | None = None
        for each in side.ranked:
            number = each.photo
            item = photos.get(number)
            if item is None:
                passed.append(f"photo {number} isn't a page photo in the library")
                continue
            if number in excluded:
                passed.append(f"the owner excluded photo {number} for this scene")
                continue
            if number in taken or any(view.number == number for view in views):
                chosen = number
                break
            if not file_store.exists(item.file):
                passed.append(f"photo {number}'s file could not be found")
                continue
            made = side_photos.true_crop(job, item, side_photos.box_of(answer, number))
            if made == side_photos.TOO_SMALL:
                passed.append(
                    f"photo {number}'s crop is too small (under {side_photos.SMALLEST_PIXELS} "
                    "px on its longest side)"
                )
                continue
            if made == side_photos.NO_BOX:
                passed.append(f"photo {number} has no box, or its box is empty or off the photo")
                continue
            views.append(View(item, made.file, side.side, each.shows.strip()))
            chosen = number
            break
        if not passed:
            continue
        why = "; ".join(passed)
        notices.post_once(
            job,
            notices.SIDE_PHOTO_PASSED_OVER,
            f"Scene {scene.number}'s {making}: the side picker's side \"{side.side}\" "
            + (
                f"got no photo: {why}."
                if chosen is None
                else f"went as photo {chosen}, past the photos ranked above it: {why}."
            ),
        )
    return views, shown


def _every_alone_view(scene: Scene, taken: set[int], making: str) -> tuple[list[View], set[str]]:
    """Every original page photo of the product alone the picks and the owner leave out, as
    sent whole, and the library sides the picks show (#516)."""
    originals = library_items.originals(scene.job)
    alone = [
        item
        for item in originals
        if isinstance(item.source, PagePhoto)
        and item.kind == "side"
        and item.alone == "yes"
        and item.number not in taken
    ]
    excluded = set(scene_references.excluded_among(scene, (item.number for item in alone)))
    views = [View(item, item.file, item.side) for item in alone if item.number not in excluded]
    missing = [view for view in views if not file_store.exists(view.file)]
    if missing:
        _left_out(
            scene,
            making,
            missing,
            "their files could not be found" if len(missing) > 1 else "its file could not be found",
        )
    shown: set[str] = {item.side for item in originals if item.number in taken}
    return [view for view in views if view not in missing], shown


def in_kept_order[T: _Sided](views: Sequence[T], *, shown: set[str], colour: str) -> list[T]:
    """`views` in the order they are kept when the image model's room runs short (Julian, Nov
    10 2026): one in the picture's `colour` for each side the picks don't show (`shown`),
    then one in any colour for each side still not shown, then the rest, each part in
    library order. A view with no side shows no side, so it is among the rest."""
    kept: list[T] = []
    for same_colour_only in (True, False):
        for view in views:
            if view.side == "none" or view.side in shown:
                continue
            if not same_colour_only or not _other_colour(view, colour):
                kept.append(view)
                shown = shown | {view.side}
    return kept + [view for view in views if view not in kept]


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
                f"Extra view, page photo {item.number}: {_shown(item, photo)}."
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
            labels.append(f"{_shown(item, photo)}." + _other_colour(item, colour))
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
    whatever views they were, so the clip judge gets the same set. A view the library no
    longer holds is left out, with a developer notice: the judge can't be told what it shows."""
    assert picture.scene is not None
    views = [
        photo
        for photo in map(SelectedPhoto.model_validate, (picture.sent or {}).get("photos", []))
        if photo.page_view
    ]
    held = set(picture.scene.job.library.values_list("number", flat=True))
    gone = sorted(
        number
        for number in (photo.library_number for photo in views)
        if number is not None and number not in held
    )
    if gone:
        numbers = ", ".join(map(str, gone))
        said = f"views {numbers} were" if len(gone) > 1 else f"view {numbers} was"
        notices.post_notice(
            picture.scene.job,
            notices.PAGE_VIEW_LEFT_OUT,
            f"Scene {picture.scene.number}'s clip: the page's {said} left out of the clip "
            "judge, as the library no longer holds " + ("them." if len(gone) > 1 else "it."),
            Message.Level.PROBLEM,
        )
    return [photo for photo in views if photo.library_number in held]


def has_views(photos: Iterable[SelectedPhoto]) -> bool:
    return any(photo.page_view for photo in photos)


def _drawing_line(view: View, colour: str) -> str:
    """The image model's line for a view: what it shows, and to take its shape, parts and
    sides, never its colour."""
    return (
        "an extra view of the product from its page: "
        f"{_words(view.item, view.side, view.shows)}. "
        "Take only its shape, parts and sides from it, never its colour."
        + _other_colour(view, colour)
    )


def _shown(item: LibraryEntry, photo: SelectedPhoto) -> str:
    """What a view's page photo shows, with the side the picker named and its line on the
    crop when the picker chose it, else the library's side."""
    return _words(item, photo.side or item.side, photo.shows)


def _words(item: LibraryEntry, side: str, shows: str | None) -> str:
    said = f"; {shows.strip().rstrip('.')}" if shows else ""
    return f"{_label(item)} (side: {'not given' if side == 'none' else side}{said})"


def _other_colour(item: _Sided, colour: str) -> str:
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


def _left_out(scene: Scene, making: str, items: Sequence[View], why: str) -> None:
    notices.post_notice(
        scene.job,
        notices.PAGE_VIEW_LEFT_OUT,
        f"Scene {scene.number}'s {making}: the page's {_views(items, 'left out')}, as {why}.",
        Message.Level.PROBLEM,
    )


def _views(items: Sequence[View], done: str = "") -> str:
    """ "view 2 was" or "views 2, 5 were", in number order, then `done`."""
    numbers = ", ".join(str(number) for number in sorted(item.number for item in items))
    said = f"views {numbers} were" if len(items) > 1 else f"view {numbers} was"
    return f"{said} {done}" if done else said
