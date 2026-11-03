"""Shared scene colour, selected-photo colour and page-video piece helpers."""

import logging
from collections.abc import Sequence
from dataclasses import dataclass

from chat.models import Message
from gateway.types import ReferenceVideo

from . import (
    library_sheets,
    notices,
    product_only,
)
from .colours import NONE
from .colours import entry as colour_entry
from .library import (
    VIDEO_API_MOST_PIECE_SECONDS,
    LibraryEntry,
    LookFrame,
    OwnerPhoto,
    SentPiece,
    watch_of,
)
from .models import Job, ReferencePhoto, Scene
from .notices import post_notice
from .planning import ScenePiece
from .product_videos import PieceWanted, cut_pieces

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PieceLeftOut:
    """A moment of a page video wanted for a clip and not sent: its video and moment, the
    moment's seconds, and why (product_videos.cut_pieces)."""

    video: int
    moment: int
    start: float
    end: float
    reason: str


def planned_colour(scene: Scene) -> str:
    """The colour `scene`'s next clip would be, from its planned piece before it is cut: its
    video's colour, else the ad's."""
    ad = ad_colour(scene.job)
    if scene.piece is None:
        return ad
    piece = ScenePiece.model_validate(scene.piece)
    video = scene.job.videos.filter(position=piece.video).first()
    watch = watch_of(video) if video is not None else None
    if watch is None:
        return ad
    return clip_colour([(watch.colour, planned_piece_seconds(scene))], ad)


def planned_piece_seconds(scene: Scene) -> float:
    """How many seconds of video the scene's planned piece sends, before anything is cut: its
    moment's seconds, at most the video API's most (an estimate's, not the cut's)."""
    if scene.piece is None:
        return 0.0
    piece = ScenePiece.model_validate(scene.piece)
    video = scene.job.videos.filter(position=piece.video).first()
    watch = watch_of(video) if video is not None else None
    if watch is None or not 1 <= piece.moment <= len(watch.moments):
        return 0.0
    moment = watch.moments[piece.moment - 1]
    return min(moment.end - moment.start, float(VIDEO_API_MOST_PIECE_SECONDS))


def in_their_own_colour(job: Job, originals: Sequence[LibraryEntry]) -> set[int]:
    """The library items among `originals` sent in their own colour, never recoloured to the
    clip's: the clean pictures of the shop owner's things (drawn from their photos, the
    scenes' ReferencePhoto rows), which aren't the product. The owner's part is part of the
    product (its source is OwnerPart), so it is recoloured like the product's photos.

    Not approved yet (#240 review, S5): this one rule says which items keep their colour."""
    photos = set(ReferencePhoto.objects.filter(scene__job=job).values_list("file", flat=True))
    return {
        item.number
        for item in originals
        if isinstance(item.source, OwnerPhoto) and item.source.original in photos
    }


def ad_colour(job: Job) -> str:
    """The ad's colour, the entry of the product's colour list the planner named: the colour
    of a clip sent no piece. As an entry: an ad planned before the list kept the planner's own
    words ("Sage green"); the library's and the watcher's colours were one word already."""
    return colour_entry(job.product_colour)


def clip_colour(pieces: Sequence[tuple[str, float]], ad: str) -> str:
    """A clip's colour (spec #210 point 7): the colour with the most seconds across its
    `pieces` (each piece's video's colour and its seconds, Video 1 first), else the ad's
    colour `ad`. A video that never shows the product's body (NONE) gives no colour. Not
    approved yet (audit item 15): a tie goes to the colour of the first piece among those
    tied, Video 1's when it is one of them."""
    pieces = [(colour, length) for colour, length in pieces if colour != NONE]
    seconds: dict[str, float] = {}
    for colour, length in pieces:
        seconds[colour] = round(seconds.get(colour, 0) + length, 3)
    if not seconds:
        return ad
    most = max(seconds.values())
    return next(colour for colour, _ in pieces if seconds[colour] == most)


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
    return None if not candidates else product_only.to_send(job, candidates[0]).number


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


def _say(job: Job, kind: notices.Kind, scene: Scene, text: str) -> None:
    """Record `text`, a notice of `kind` about `scene`, in red, unless the job was told
    exactly that already: a step run again, as after a worker stopped, tells nothing twice."""
    job.refresh_from_db(fields=["warnings"])
    if any(warning["text"] == text for warning in job.warnings):
        return
    logger.warning("Job %s: %s", job.pk, text)
    post_notice(job, kind, text, Message.Level.PROBLEM, scene=scene.number)


def _say_piece_left_out(
    job: Job, scene: Scene, piece: ScenePiece, start: float, end: float, reason: str
) -> None:
    _say(
        job,
        notices.PIECE_NOT_SENT,
        scene,
        f"Scene {scene.number}'s clip: the piece of video {piece.video}, moment {piece.moment} "
        f"({start:g} to {end:g} s), isn't sent: {reason}. The clip is made without that "
        "movement to copy.",
    )
