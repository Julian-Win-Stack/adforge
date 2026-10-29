"""Cut-and-send (spec #210 points 7, 13, 14, 17): the code step that builds what a B-roll
clip is sent, the same way every time, before every send. No AI prunes or combines anything.

`cut_and_send(scene, person_shown=..., picks=..., pieces=...)` gives a `Sending`:
- The pictures, in tiers (point 13). Tier 1, Image 1: the product's front look photo, always
  a page photo (`image_1`). Tier 2: the picks, the planner's scene pictures in its order or
  the fixing agent's named references, no cap. Tier 3: the drawn picture of the place, the
  presenter in the room or the room alone, the scene's own when it has one
  (work.scene_place). The scene's owner part picture, when it holds one, goes last in tier 2
  (owner_parts.part_picture), and no other scene is sent it, nor a picture made from it
  (owner_parts.part_not_held: such a pick is refused). Tier 4: the other side pictures
  (back, side, top) in library order, but owner parts' and pictures the producer made
  (ONLY_WHERE_NAMED): those go only where they are picked.
  The scene's exclude list (jobs.scene_references) holds in every tier: an excluded item is
  never sent, takes no place under the cap, and is named in the Sending's `excluded`.
  States and pack sizes are in no tier: they go only when picked. The first
  VIDEO_API_MOST_PICTURES are sent; the rest are left out, their tier the reason, so a tier-4
  photo never pushes out a pick. A sender chooses among the library's originals only, and
  sends for each its product-only version when it has one (library_items.to_send).
- The pieces (point 14): each moment named cut from its video at the watcher's seconds and
  trimmed to the video API's window (product_videos.cut_pieces).
- The clip's colour (point 7, `clip_colour`), and every library picture sent recoloured to it
  (jobs.recolour) from a colour reference (`colour_reference`), so the pictures sent are the
  recoloured files: no picture in another colour reaches the video API by any path.
- A red notice for the scene, with its warning line, for each thing that didn't go as
  planned: pictures left out, pictures not recoloured for want of a reference, pieces left
  out, no Image 1, no drawn picture; each said once. A recolour that fell back is said by
  the recolour step, once per picture and colour.

`planned_sending(step)` is the first send's: the scene's planned pictures and piece.
`first_sent_list(step, sending)` writes the first send's prompt (jobs.first_send) and gives
the clip's SentList, to keep on the clip with library.keep_sent_list. A retry builds its
own: `sending.sent_list(prompt)` with the fixing agent's prompt.

Run again, as after a worker stopped, it pays for nothing twice: pieces are cut once and
recolours made once per picture and colour."""

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal

from chat.models import Message
from gateway.types import ReferenceVideo

from . import library_items, library_sheets, owner_parts, scene_references, work
from .first_send import FirstSendScene, drawn_label, write_first_send_prompt
from .library import (
    VIDEO_API_MOST_PICTURES,
    Drawn,
    For,
    LeftOut,
    LibraryEntry,
    LookFrame,
    OwnerPhoto,
    PagePhoto,
    ProductOnly,
    Recolour,
    Recoloured,
    SentList,
    SentPicture,
    SentPiece,
    library_of,
    watch_of,
)
from .library_items import LOOK_KINDS
from .models import Job, Scene, SceneStep
from .notices import post_notice
from .planning import ScenePicture, ScenePiece
from .product_videos import PieceWanted, cut_pieces
from .recolour import recolour

logger = logging.getLogger(__name__)

Tier = Literal[1, 2, 3, 4]

# The sides tier 4 sends, after the front (spec #210 point 13).
OTHER_SIDES = ("back", "side", "top")

# Why a picture of each tier was taken, as a notice says it.
TIER_NAMES: dict[Tier, str] = {
    1: "the product's front photo",
    2: "a scene picture",
    3: "the drawn picture of the place",
    4: "another side of the product",
}
DRAWN_NAMES: dict[Drawn, str] = {
    "person_in_room": "the presenter in the ad's room",
    "room": "the ad's room alone",
}


@dataclass(frozen=True)
class PieceLeftOut:
    """A moment of a page video wanted for a clip and not sent: its video and moment, the
    moment's seconds, and why (product_videos.cut_pieces)."""

    video: int
    moment: int
    start: float
    end: float
    reason: str


@dataclass(frozen=True)
class Excluded:
    """A library item a tier would have taken for a clip, not sent: the scene excludes it.
    `number` is the original's (a pick of a version is a pick of its original)."""

    number: int
    label: str
    tier: Tier


@dataclass(frozen=True)
class Sending:
    """What one clip is sent, built by cut-and-send: everything of its SentList but the
    prompt, and the pieces as the video API's handoff takes them."""

    colour: str
    pictures: list[SentPicture]
    pieces: list[SentPiece]
    left_out: list[LeftOut]
    recolours: list[Recolour]
    reference_videos: list[ReferenceVideo]
    # The pieces wanted and left out, for the clip's answer (agents.hard_list.cut_answer).
    pieces_left_out: list[PieceLeftOut] = field(default_factory=list)
    # The items the scene's exclude list kept out, for the clip's answer.
    excluded: list[Excluded] = field(default_factory=list)

    @property
    def example_pictures(self) -> list[str]:
        """The pictures' files in order, Image 1 first: the handoff's example pictures."""
        return [picture.file for picture in self.pictures]

    def sent_list(self, prompt: str) -> SentList:
        """The clip's record, sent with `prompt`."""
        return SentList(
            colour=self.colour,
            pictures=self.pictures,
            pieces=self.pieces,
            left_out=self.left_out,
            recolours=self.recolours,
            prompt=prompt,
        )


@dataclass(frozen=True)
class _Taken:
    """A picture taken for a clip, before the cut: a library original and the item that
    goes for it, or the drawn picture."""

    tier: Tier
    original: LibraryEntry | None
    item: LibraryEntry | None
    drawn: Drawn | None
    for_: For | None
    label: str
    file: str


def planned_sending(step: SceneStep) -> Sending:
    """What the first send of `step`'s clip is sent: its scene's planned pictures and piece."""
    scene = step.scene
    return cut_and_send(
        scene,
        person_shown=step.person_shown,
        picks=[ScenePicture.model_validate(picture) for picture in scene.scene_pictures],
        pieces=[] if scene.piece is None else [ScenePiece.model_validate(scene.piece)],
    )


def first_sent_list(step: SceneStep, sending: Sending) -> SentList:
    """The SentList of `step`'s clip sent `sending` the first time: with the first send's
    prompt, written now (one paid call)."""
    prompt = write_first_send_prompt(
        step.scene.job,
        sending.pictures,
        sending.pieces,
        FirstSendScene(
            line=step.line,
            shows=step.shows,
            broll_kind=step.broll_kind,
            usage=step.usage,
            result=step.broll_result,
        ),
    )
    return sending.sent_list(prompt)


def cut_and_send(
    scene: Scene,
    *,
    person_shown: str,
    picks: Sequence[ScenePicture],
    pieces: Sequence[ScenePiece],
) -> Sending:
    """What a clip of `scene`, showing `person_shown`, is sent with `picks` as its tier 2
    and the moments `pieces` as its pieces: see the module doc."""
    job = scene.job
    originals = library_items.originals(job)
    sent_pieces, reference_videos, colours, pieces_left_out = _pieces(scene, pieces)
    ad = ad_colour(job)
    colour = clip_colour(colours, ad)
    taken, excluded = _in_tiers(scene, person_shown, originals, picks)
    sent, left = taken[:VIDEO_API_MOST_PICTURES], taken[VIDEO_API_MOST_PICTURES:]
    colour_video = next(
        (
            piece.video
            for piece, (said, _) in zip(sent_pieces, colours, strict=True)
            if colour_word(said) == colour
        ),
        None,
    )
    reference = colour_reference(job, originals, colour_video, in_ad_colour=colour == ad)
    in_ad_colour = (
        {item.number for item in _colour_photos(job, originals)} if colour == ad else set()
    )
    pictures: list[SentPicture] = []
    recolours: list[Recolour] = []
    fell_back: list[LibraryEntry] = []
    for image, picture in enumerate(sent, start=1):
        item = picture.item
        if (
            item is not None
            and picture.original is not None
            and colour_word(item.colour) != colour
            and picture.original.number not in in_ad_colour
            and item.number != reference
        ):
            # A recolour that falls back is said by the recolour step, once per picture and
            # colour; one never tried, for want of a reference, is said here.
            if reference is None:
                record = Recolour(of=item.number, colour=colour, outcome="fell_back", made=None)
                fell_back.append(item)
            else:
                made = recolour(job, item.number, colour, reference=reference, scene=scene.number)
                record, item = made.record, made.picture
            recolours.append(record)
        pictures.append(
            SentPicture(
                image=image,
                tier=picture.tier,
                library_number=None if item is None else item.number,
                drawn=picture.drawn,
                for_=picture.for_,
                label=picture.label if item is None else item.label,
                file=picture.file if item is None else item.file,
            )
        )
    left_out = [
        LeftOut(
            tier=picture.tier,
            library_number=None if picture.item is None else picture.item.number,
            drawn=picture.drawn,
            for_=picture.for_,
            label=picture.label,
            file=picture.file,
        )
        for picture in left
    ]
    if left_out:
        _say_left_out(job, scene, left_out)
    if fell_back:
        _say_no_reference(job, scene, colour, fell_back, colour_video, in_ad_colour=colour == ad)
    return Sending(
        colour=colour,
        pictures=pictures,
        pieces=sent_pieces,
        left_out=left_out,
        recolours=recolours,
        reference_videos=reference_videos,
        pieces_left_out=pieces_left_out,
        excluded=excluded,
    )


@dataclass(frozen=True)
class NextSend:
    """What a clip of a scene would be sent as its references stand, before anything is cut
    or recoloured: the pictures in order (their labels before any recolour), those the cap
    leaves out, and those the scene excludes. Costs nothing."""

    pictures: list[LeftOut]
    left_out: list[LeftOut]
    excluded: list[Excluded]


def next_send(scene: Scene, *, person_shown: str) -> NextSend:
    """What `scene`'s next clip would be sent from its reference list, as cut-and-send
    orders and cuts it, said to nobody: for the producer's set_references answer."""
    originals = library_items.originals(scene.job)
    picks = [ScenePicture.model_validate(picture) for picture in scene.scene_pictures]
    taken, excluded = _in_tiers(scene, person_shown, originals, picks, say=False)
    refs = [
        LeftOut(
            tier=picture.tier,
            library_number=None if picture.item is None else picture.item.number,
            drawn=picture.drawn,
            for_=picture.for_,
            label=picture.label,
            file=picture.file,
        )
        for picture in taken
    ]
    return NextSend(
        pictures=refs[:VIDEO_API_MOST_PICTURES],
        left_out=refs[VIDEO_API_MOST_PICTURES:],
        excluded=excluded,
    )


def colour_word(colour: str) -> str:
    """A colour in the one word form colours are compared in: lower case, its words joined
    by hyphens ("Sage green" is "sage-green"). Two colours are the same only when their
    words are: no colour is matched to another."""
    return "-".join(colour.lower().split())


def ad_colour(job: Job) -> str:
    """The ad's colour, as the planner named it, as one word: the colour of a clip sent no
    piece."""
    return colour_word(job.product_colour)


def clip_colour(pieces: Sequence[tuple[str, float]], ad: str) -> str:
    """A clip's colour (spec #210 point 7): the colour with the most seconds across its
    `pieces` (each piece's video's colour and its seconds, Video 1 first), else the ad's
    colour `ad`. Not approved yet (audit item 15): a tie goes to the colour of the first
    piece among those tied, Video 1's when it is one of them."""
    pieces = [(colour_word(colour), length) for colour, length in pieces]
    seconds: dict[str, float] = {}
    for colour, length in pieces:
        seconds[colour] = round(seconds.get(colour, 0) + length, 3)
    if not seconds:
        return ad
    most = max(seconds.values())
    return next(colour for colour, _ in pieces if seconds[colour] == most)


def front_of(originals: Sequence[LibraryEntry]) -> LibraryEntry | None:
    """The tier 1 picture, Image 1 of every clip: the page's front look photo, else an
    owner's front photo standing in; None when there is neither."""
    return image_1(originals) or owner_front(originals)


def original_of_number(job: Job, number: int) -> LibraryEntry:
    """The library original item `number` of `job` is a version of, or the item itself: how
    cut-and-send reads a pick."""
    everything = {item.number: item for item in library_of(job)}
    return _original_of(everything, everything[number])


def image_1(originals: Sequence[LibraryEntry]) -> LibraryEntry | None:
    """Tier 1, Image 1: the product's front look photo, always a page photo; the first look
    photo in library order when the page has no front. Not approved yet (audit item 10):
    which picture goes when the page has no front. None when the page has no look photo."""
    looks = _looks(originals, PagePhoto)
    return next(iter(_fronts(looks) or looks), None)


def owner_front(originals: Sequence[LibraryEntry]) -> LibraryEntry | None:
    """Image 1 when the page has no look photo: the shop owner's front look photo. Not
    approved yet (next to audit item 10): spec #210 point 13 says Image 1 is always a page
    photo; this stands in when there is none. None when the owner sent no front either."""
    return next(iter(_fronts(_looks(originals, OwnerPhoto))), None)


def _looks(
    originals: Sequence[LibraryEntry], source: type[PagePhoto] | type[OwnerPhoto]
) -> list[LibraryEntry]:
    return [
        item for item in originals if isinstance(item.source, source) and item.kind in LOOK_KINDS
    ]


def _fronts(looks: Sequence[LibraryEntry]) -> list[LibraryEntry]:
    """The front look photos of `looks`, those of the product's sides before its states."""
    fronts = [item for item in looks if item.side == "front"]
    return [item for item in fronts if item.kind == "side"] or fronts


def colour_reference(
    job: Job, originals: Sequence[LibraryEntry], colour_video: int | None, *, in_ad_colour: bool
) -> int | None:
    """The library number of the picture a clip's pictures are recoloured from: a look frame
    of the page video `colour_video`, the one whose piece gave the clip its colour, when a
    piece is sent; else, or when that video has no look frame and the clip is `in_ad_colour`,
    the first of the planner's colour photos. Not approved yet (audit item 14): the reference
    when no piece is sent. None when there is none."""
    candidates: list[LibraryEntry] = []
    if colour_video is not None:
        candidates = [
            item
            for item in originals
            if isinstance(item.source, LookFrame) and item.source.video == colour_video
        ]
    if not candidates and (colour_video is None or in_ad_colour):
        candidates = _colour_photos(job, originals)
    return None if not candidates else library_items.to_send(job, candidates[0]).number


def _colour_photos(job: Job, originals: Sequence[LibraryEntry]) -> list[LibraryEntry]:
    """The originals the planner named as its colour photos, showing the ad's colour."""
    positions = library_sheets.photo_positions(job)
    marked = set(job.photos.filter(shows_product_colour=True).values_list("position", flat=True))
    return [item for item in originals if positions.get(item.number) in marked]


def _pieces(
    scene: Scene, pieces: Sequence[ScenePiece]
) -> tuple[list[SentPiece], list[ReferenceVideo], list[tuple[str, float]], list[PieceLeftOut]]:
    """Each moment of `pieces` cut from its video: the pieces sent, as the record and as the
    handoff keeps them, each one's video's colour and seconds, and the pieces left out. A
    piece left out is said."""
    job = scene.job
    named = []
    for piece in pieces:
        video = job.videos.filter(position=piece.video).first()
        if video is None:
            raise ValueError(f"the ad has no video {piece.video}")
        watch = watch_of(video)
        if watch is None or not 1 <= piece.moment <= len(watch.moments):
            raise ValueError(f"video {piece.video} has no moment {piece.moment}")
        moment = watch.moments[piece.moment - 1]
        named.append(
            (piece, moment, watch.colour, PieceWanted(video.file, moment.start, moment.end))
        )
    cut = cut_pieces([wanted for *_, wanted in named])
    left = {id(piece.wanted): piece.reason for piece in cut.left_out}
    cuts = iter(cut.sent)
    sent: list[SentPiece] = []
    colours: list[tuple[str, float]] = []
    left_out: list[PieceLeftOut] = []
    for piece, moment, colour, wanted in named:
        if id(wanted) in left:
            _say_piece_left_out(job, scene, piece, moment.start, moment.end, left[id(wanted)])
            left_out.append(
                PieceLeftOut(piece.video, piece.moment, moment.start, moment.end, left[id(wanted)])
            )
            continue
        made = next(cuts)
        sent.append(
            SentPiece(
                number=len(sent) + 1,
                video=piece.video,
                moment=piece.moment,
                moment_start=moment.start,
                moment_end=moment.end,
                start=made.start,
                end=made.end,
                trim=made.trim,
                file=made.file,
            )
        )
        colours.append((colour, made.seconds))
    return sent, list(cut.sent), colours, left_out


# The library items tier 4 never takes, however they are labelled: an owner part's picture
# and a picture the producer made (spec #247 point 5) go only where they are named.
ONLY_WHERE_NAMED = ("owner_part", "made")


def _in_tiers(
    scene: Scene,
    person_shown: str,
    originals: Sequence[LibraryEntry],
    picks: Sequence[ScenePicture],
    *,
    say: bool = True,
) -> tuple[list[_Taken], list[Excluded]]:
    """Every picture a clip takes, tier by tier, before the cut; each library original once,
    in the first tier that takes it; and the items the scene's exclude list kept out, each
    once, in the first tier that would have taken it. With `say` false nothing missing is
    said."""
    job = scene.job
    everything = {item.number: item for item in library_of(job)}
    out = scene_references.excluded(scene)
    taken: list[_Taken] = []
    excluded: list[Excluded] = []
    seen: set[int] = set()

    def take(tier: Tier, original: LibraryEntry, for_: For | None = None) -> None:
        if original.number in seen:
            return
        seen.add(original.number)
        if original.number in out:
            excluded.append(Excluded(original.number, original.label, tier))
            return
        item = library_items.to_send(job, original)
        taken.append(_Taken(tier, original, item, None, for_, item.label, item.file))

    unexcluded = front_of(originals)
    if unexcluded is not None and unexcluded.number in out:
        take(1, unexcluded)
    front = front_of([item for item in originals if item.number not in out])
    if front is None:
        if say:
            _say_no_front(job, scene)
    else:
        take(1, front)
    for pick in picks:
        if pick.number not in everything:
            raise ValueError(f"library item {pick.number} isn't in the ad's library")
        original = _original_of(everything, everything[pick.number])
        if (part := owner_parts.part_not_held(scene, original, everything)) is not None:
            raise ValueError(
                f"library item {pick.number} shows the owner part pictured in library item "
                f"{part.number}, which scene {scene.number} doesn't hold"
            )
        take(2, original, pick.for_)
    # The scene's owner part, as it stands: a picture the scene holds, so a scene picture.
    if (part := owner_parts.part_picture(scene)) is not None:
        take(2, _original_of(everything, part))
    drawn, place = work.scene_place(scene, person_shown)
    if place is None:
        if say:
            _say_no_place(job, scene, drawn)
    else:
        taken.append(_Taken(3, None, None, drawn, None, _drawn_label(job, drawn), place.file))
    for item in originals:
        # An owner part's picture goes only to the scenes holding the part, as tier 2, and a
        # made picture only where it is named.
        if item.source.came_from in ONLY_WHERE_NAMED:
            continue
        if item.kind == "side" and item.side in OTHER_SIDES:
            take(4, item)
    return taken, excluded


def _original_of(everything: dict[int, LibraryEntry], item: LibraryEntry) -> LibraryEntry:
    """The original `item` is a version of (a product-only or recoloured version), or itself:
    a pick of a version is a pick of its original, sent as to_send and recolour make it."""
    while isinstance(item.source, ProductOnly | Recoloured):
        item = everything[item.source.of]
    return item


def _drawn_label(job: Job, drawn: Drawn) -> str:
    """The drawn picture's label, as the prompt's line for it says it."""
    words = drawn_label(job, drawn)
    return f"{words[0].upper()}{words[1:]}."


def _named(picture: LeftOut) -> str:
    where = (
        f"library item {picture.library_number}"
        if picture.library_number is not None
        else DRAWN_NAMES[picture.drawn]
        if picture.drawn is not None
        else "a picture"
    )
    return f'"{picture.label}" ({where}, {TIER_NAMES[picture.tier]})'


def _say(job: Job, text: str) -> None:
    """Say `text` in red, with its warning line, unless the job was told exactly that
    already: a step run again, as after a worker stopped, tells nothing twice."""
    job.refresh_from_db(fields=["warnings"])
    if any(warning["text"] == text for warning in job.warnings):
        return
    logger.warning("Job %s: %s", job.pk, text)
    post_notice(job, text, Message.Level.PROBLEM)


def _say_left_out(job: Job, scene: Scene, left_out: Sequence[LeftOut]) -> None:
    many = len(left_out) != 1
    _say(
        job,
        f"Scene {scene.number}'s clip: the video model takes at most "
        f"{VIDEO_API_MOST_PICTURES} pictures, so {len(left_out)} picture{'s' if many else ''} "
        f"{'were' if many else 'was'} left out: {'; '.join(_named(p) for p in left_out)}. "
        f"The clip is made without {'them' if many else 'it'}, so it may not show what "
        f"{'they show' if many else 'it shows'}.",
    )


def _say_no_reference(
    job: Job,
    scene: Scene,
    colour: str,
    fell_back: Sequence[LibraryEntry],
    colour_video: int | None,
    *,
    in_ad_colour: bool,
) -> None:
    """The red notice for the pictures not recoloured to `colour` for want of a reference."""
    if colour_video is None:
        why = f"the plan names no colour photo showing the product in {colour}"
    else:
        why = f"page video {colour_video}, which gives the clip its colour, has no look frame"
        if in_ad_colour:
            why += " and the plan names no colour photo"
    many = len(fell_back) != 1
    named = "; ".join(
        f'library item {item.number} ("{item.label}", {item.colour})' for item in fell_back
    )
    _say(
        job,
        f"Scene {scene.number}'s clip is in {colour}, but {len(fell_back)} of its pictures "
        f"went in {'their' if many else 'its'} own colour, as {why} to take the colour from: "
        f"{named}. The clip may show the product in two colours.",
    )


def _say_piece_left_out(
    job: Job, scene: Scene, piece: ScenePiece, start: float, end: float, reason: str
) -> None:
    _say(
        job,
        f"Scene {scene.number}'s clip: the piece of video {piece.video}, moment {piece.moment} "
        f"({start:g} to {end:g} s), isn't sent: {reason}. The clip is made without that "
        "movement to copy.",
    )


def _say_no_place(job: Job, scene: Scene, drawn: Drawn) -> None:
    _say(
        job,
        f"Scene {scene.number}'s clip is sent no picture of {DRAWN_NAMES[drawn]}: the ad "
        "doesn't have one yet, so the video model makes up the place.",
    )


def _say_no_front(job: Job, scene: Scene) -> None:
    _say(
        job,
        f"Scene {scene.number}'s clip is sent no photo of the product's look as Image 1: the "
        "ad's picture library has no photo of the product from the front or of its look, from "
        "the page or the shop owner. The video model sees the product only in the other "
        "pictures, so it may get its look wrong.",
    )
