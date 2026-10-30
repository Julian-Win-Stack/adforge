"""The shapes spec #210's build shares (#211): the library item, the video moment and the
sent list. Each is a schema owned here and checked at the boundary: where a model's answer
comes in, and where a record is stored or read back.

Numbering, the contract the planner and cut-and-send build on:
- library numbers run in the order items enter the job's library, from 1, and never change;
- moments are numbered per video, from 1, in time order.

Every fixed value set is one Literal here, so changing one is a one-line edit with no
migration: the database keeps the values as plain text. The set is checked strictly where a
value comes in (a model's answer, a record being stored), and leniently where a stored record
is read back (`_STORED`): a value the set no longer holds comes back as it was kept, with a log
line, so rows written before a value was renamed or removed stay readable. Code that reads a
stored shape must take a fixed value it doesn't know as just another value."""

import logging
from collections.abc import Sequence
from typing import Annotated, Any, Literal, Self

from django.db import transaction
from django.db.models import Max
from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    ValidationInfo,
    ValidatorFunctionWrapHandler,
    WrapValidator,
    model_validator,
)

from gateway.types import MOST_EXAMPLE_PICTURES, MOST_PIECE_SECONDS, PieceTrim
from jobs import cards
from jobs.models import Job, LibraryItem, ProducedItem, ProductVideo

logger = logging.getLogger(__name__)

# The validation context a stored record is read back with: see the module doc.
_STORED = {"stored": True}


def _lenient_when_stored(
    value: Any, handler: ValidatorFunctionWrapHandler, info: ValidationInfo
) -> Any:
    """A fixed value checked against its set, or, read back from a stored record, kept as it
    was when the set no longer holds it."""
    try:
        return handler(value)
    except ValidationError:
        if not (info.context or {}).get("stored") or not isinstance(value, str | int):
            raise
        logger.warning("a stored record holds %r, which its value set no longer has", value)
        return value


Fixed = WrapValidator(_lenient_when_stored)

# The picker's fixed fields (#210 point 2). Not approved yet: the look-frame rule (audit
# item 9) may change these.
Kind = Annotated[Literal["side", "state", "content", "in-use", "result", "line-up", "chart"], Fixed]
Side = Annotated[Literal["front", "back", "side", "top", "none"], Fixed]
Alone = Annotated[Literal["yes", "no"], Fixed]

# The video API's limits on one clip's call (#210 point 13). Its limit on pieces is the
# gateway's, where the call is checked.
VIDEO_API_MOST_PICTURES = MOST_EXAMPLE_PICTURES
VIDEO_API_MOST_PIECE_SECONDS = MOST_PIECE_SECONDS

# The sent list's fixed values (#210 points 11, 13, 14, 8). Not approved yet: the planner's
# `for` values (audit item 11).
For = Annotated[Literal["content", "result", "in-use", "place", "person", "state"], Fixed]
Tier = Annotated[Literal[1, 2, 3, 4], Fixed]
Drawn = Annotated[Literal["person_in_room", "room"], Fixed]
# Owned by the gateway, where a piece is handed to the video API with its trim.
Trim = Annotated[PieceTrim, Fixed]
RecolourOutcome = Annotated[Literal["done", "fell_back"], Fixed]


class _Shape(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _one_word(colour: str) -> str:
    """A colour as one lowercase word ("red", "rose-gold"), refused when it is not one."""
    word = colour.strip().lower()
    if not word or any(character.isspace() for character in word):
        raise ValueError(f"a colour is one word, not {colour!r}")
    return word


Colour = Annotated[str, AfterValidator(_one_word)]
Sentence = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
# A file store key: kept exactly as given, never stripped.
Key = Annotated[str, StringConstraints(min_length=1)]


class PictureLabel(_Shape):
    """The picker's fixed fields for one picture: what the labelling call answers for an
    owner photo too, and what every library item carries."""

    kind: Kind
    side: Side
    alone: Alone = Field(description="yes: the product alone on a plain background.")
    colour: Colour = Field(description="The product's colour in the picture, one short word.")
    target: str = Field(
        default="", description="For a line-up, the product's position in it; else empty."
    )
    label: Sentence = Field(
        description="One sentence in the prompt's form, such as \"The bottle, lid off, pump "
        'and a drip showing." Read by models only: code never parses it.'
    )

    @model_validator(mode="after")
    def _target_only_for_a_line_up(self) -> Self:
        if (self.kind == "line-up") != bool(self.target.strip()):
            raise ValueError("a line-up names its target, and nothing else has one")
        return self


class PagePhoto(_Shape):
    """A kept page photo: its product photo's position on the job."""

    came_from: Literal["page_photo"] = "page_photo"
    photo: int


class OwnerPhoto(_Shape):
    """A photo the shop owner sent, cleaned: the item's file is the clean picture and
    `original` the file store key of the photo as sent."""

    came_from: Literal["owner_photo"] = "owner_photo"
    original: Key


class LookFrame(_Shape):
    """A look frame: the page video's position on the job and the second it was pulled at."""

    came_from: Literal["look_frame"] = "look_frame"
    video: int
    second: float = Field(ge=0)


class OwnerPart(_Shape):
    """The picture of an owner part (a part the page doesn't show): the part's `what`, as the
    scene's owner part names it."""

    came_from: Literal["owner_part"] = "owner_part"
    part: Sentence


class ProductOnly(_Shape):
    """The product-only version of another item: that item's library number."""

    came_from: Literal["product_only"] = "product_only"
    of: int = Field(ge=1)


class Recoloured(_Shape):
    """Another item recoloured to `colour`, which is also this item's own colour."""

    came_from: Literal["recoloured"] = "recoloured"
    of: int = Field(ge=1)
    colour: Colour


class FaceSwapped(_Shape):
    """Another item, a photo showing a stranger's face, with every face in it redrawn as the
    presenter's (#275): that item's library number, and the file store key of the presenter's
    portrait it was redrawn from, so a presenter made again makes a new one."""

    came_from: Literal["face_swapped"] = "face_swapped"
    of: int = Field(ge=1)
    portrait: Key


class Made(_Shape):
    """A picture the producer made with the picture model (spec #247 point 5): from its own
    prompt, with these library items, by number, as references (none when drawn from the
    prompt alone)."""

    came_from: Literal["made"] = "made"
    references: list[Annotated[int, Field(ge=1)]]


Source = Annotated[
    PagePhoto | OwnerPhoto | LookFrame | OwnerPart | ProductOnly | Recoloured | FaceSwapped | Made,
    Field(discriminator="came_from"),
]
# The sources of an item that is a version of another, by its `of`: a pick or an exclude of a
# version is one of its original.
Version = ProductOnly | Recoloured | FaceSwapped


class LibraryEntry(PictureLabel):
    """One library item: a picture or look frame the app knows about for one job."""

    number: int = Field(ge=1)
    file: Key
    source: Source

    @model_validator(mode="after")
    def _a_version_of_an_earlier_item(self) -> Self:
        if isinstance(self.source, Version) and self.source.of >= self.number:
            raise ValueError("an item is a version of an earlier item")
        if isinstance(self.source, Made) and any(
            reference >= self.number for reference in self.source.references
        ):
            raise ValueError("a made picture's references are earlier items")
        if isinstance(self.source, Recoloured) and self.source.colour != self.colour:
            raise ValueError("a recoloured item is in the colour it was recoloured to")
        return self


class WatchedMoment(_Shape):
    """One action in a page video, as the watcher writes it: its seconds in the video, what
    happens in product terms and what is on screen (the product's state, the hands, the
    output). The watcher is held to the video API's limit (VIDEO_API_MOST_PIECE_SECONDS) in
    its own answer's schema; a stored moment may run longer, and cut-and-send trims it."""

    start: float = Field(ge=0, description="The second it starts at.")
    end: float = Field(description="The second it ends at.")
    happens: Sentence = Field(description="What happens, in product terms.")
    on_screen: Sentence = Field(
        description="What is on screen: the product's state, the hands, the output."
    )

    @model_validator(mode="after")
    def _ends_after_it_starts(self) -> Self:
        if self.end <= self.start:
            raise ValueError("a moment ends after it starts")
        return self


class Moment(WatchedMoment):
    """A moment as the library keeps it, numbered: what the planner and the fixing agent
    name a piece by, with its video."""

    number: int


class Span(_Shape):
    """Some seconds of a page video, from `start` to `end`."""

    start: float = Field(ge=0)
    end: float


# Why the watcher's safety filter refused a video (requirement 22): its sound, some of its
# pictures, all of it, or nothing found when it was asked again and let it through.
RefusedFor = Literal["asked again", "sound", "pictures", "all"]


class FilterRefusal(_Shape):
    """The record of the watcher's safety filter refusing a video: why, the seconds dropped
    (none when only its sound was) and how many one-word probes the search for them took."""

    reason: RefusedFor
    dropped: list[Span]
    probes: int = Field(ge=0)


class VideoWatch(_Shape):
    """What the watcher saw in one page video, kept on it (ProductVideo.watched): its
    moments, numbered from 1 in time order, its look frames (one second per product state it
    shows), the product's colour in it and what is said in it, and the record of its safety
    filter refusing any of it."""

    moments: list[Moment]
    look_frames: list[Annotated[float, Field(ge=0)]]
    colour: Colour
    transcript: str
    refused: FilterRefusal | None = None

    @model_validator(mode="after")
    def _numbered_in_time_order(self) -> Self:
        if self.moments != number_moments(list(self.moments)):
            raise ValueError("moments are numbered from 1 in time order")
        return self


def number_moments(moments: Sequence[WatchedMoment]) -> list[Moment]:
    """`moments` numbered from 1 in time order: the numbering rule."""
    in_order = sorted(moments, key=lambda moment: (moment.start, moment.end))
    return [
        Moment(number=number, **moment.model_dump(include=set(WatchedMoment.model_fields)))
        for number, moment in enumerate(in_order, start=1)
    ]


def keep_watch(video: ProductVideo, watch: VideoWatch) -> None:
    """Keep `watch` on `video`."""
    video.watched = watch.model_dump()
    video.save(update_fields=["watched"])


def watch_of(video: ProductVideo) -> VideoWatch | None:
    """What the watcher saw in `video`, read back through its schema; None when unwatched."""
    return (
        None if video.watched is None else VideoWatch.model_validate(video.watched, context=_STORED)
    )


class PictureRef(_Shape):
    """A picture cut-and-send took for a clip, sent or left out: a library item by number,
    or the one drawn picture (tier 3, the person in the room or the room alone), with its
    label and the file that went (the recoloured file when it was recoloured)."""

    model_config = ConfigDict(validate_by_name=True, serialize_by_alias=True)

    tier: Tier = Field(
        description="1 the front look photo, 2 the planner's or the fixing agent's pick, 3 "
        "the drawn picture, 4 another side photo. For a left-out picture, why it was left out."
    )
    library_number: int | None = Field(ge=1, description="Its library number; none if drawn.")
    drawn: Drawn | None = Field(description="Which drawn picture it is; none if from the library.")
    for_: For | None = Field(
        alias="for", description="For a tier 2 pick, what it is for, if it was given one."
    )
    label: Sentence
    file: Key = Field(description="Key of the file that went, in the file store.")

    @model_validator(mode="after")
    def _a_library_item_or_the_drawn_picture(self) -> Self:
        if (self.library_number is None) == (self.drawn is None):
            raise ValueError("a picture is a library item or the drawn picture, not both")
        if (self.drawn is not None) != (self.tier == 3):
            raise ValueError("the drawn picture is tier 3, and only it is")
        if self.for_ is not None and self.tier != 2:
            raise ValueError("only a tier 2 pick says what it is for")
        return self


class SentPicture(PictureRef):
    """A picture sent: Image `image` of the clip's call."""

    image: int


class LeftOut(PictureRef):
    """A picture left out of the video API's cap; its tier is why."""


class StrangerLeftOut(PictureRef):
    """A photo a tier took, not sent: it shows a stranger's face that couldn't be changed to
    the presenter's (#275, jobs.face_swap). Its file is the photo's own, never sent."""


class SentPiece(_Shape):
    """A piece of a page video sent as Video `number`: the moment it was cut from, the
    moment's seconds, the seconds cut and why they were trimmed, if they were."""

    number: int
    video: int = Field(ge=1, description="The page video's position on the job.")
    moment: int = Field(ge=1, description="The moment's number in its video.")
    moment_start: float = Field(ge=0)
    moment_end: float
    start: float = Field(ge=0, description="The second the piece starts at, in its video.")
    end: float = Field(description="The second the piece ends at, in its video.")
    trim: Trim | None = Field(
        description="moment_over_limit: the moment ran past the video API's limit and was cut "
        "to its first seconds; pieces_over_limit: the pieces ran past it together and the last "
        "was cut to fit; video_ended: the moment ran past its video's end and was cut there. "
        "None for the moment's own seconds."
    )
    file: Key = Field(description="Key of the cut piece in the file store.")

    @property
    def seconds(self) -> float:
        return self.end - self.start

    @model_validator(mode="after")
    def _the_moments_seconds_or_a_trim_of_them(self) -> Self:
        if self.trim is None and (self.start, self.end) != (self.moment_start, self.moment_end):
            raise ValueError("an untrimmed piece is its moment's seconds")
        if not self.moment_start <= self.start < self.end <= self.moment_end:
            raise ValueError("a piece is cut inside its moment")
        return self


class Recolour(_Shape):
    """One picture recoloured to the clip's colour: done, with the library number of what
    was made, or fallen back to the original."""

    of: int = Field(ge=1, description="The library number of the picture recoloured.")
    colour: Colour
    outcome: RecolourOutcome
    made: int | None = Field(ge=1, description="The recoloured item's number, when done.")

    @model_validator(mode="after")
    def _done_made_something(self) -> Self:
        if (self.outcome == "done") != (self.made is not None):
            raise ValueError("a done recolour made an item, and a fallen-back one didn't")
        return self


def _numbered_from_1(numbers: list[int]) -> bool:
    return numbers == list(range(1, len(numbers) + 1))


class SentList(_Shape):
    """What one clip was sent, owned by cut-and-send and kept on the clip's produced item
    (ProducedItem.sent): the pictures in order, the pieces, every picture left out with its
    tier, every recolour and its outcome, every photo kept out for a stranger's face, and the
    prompt."""

    colour: Colour = Field(description="The clip's colour, which every picture was sent in.")
    pictures: list[SentPicture]
    pieces: list[SentPiece]
    left_out: list[LeftOut]
    recolours: list[Recolour]
    prompt: Sentence
    # A clip sent before #275 kept none.
    strangers_left_out: list[StrangerLeftOut] = Field(default_factory=list)

    @model_validator(mode="after")
    def _within_the_video_apis_limits(self) -> Self:
        if not _numbered_from_1([picture.image for picture in self.pictures]):
            raise ValueError("pictures are Images 1, 2, 3... in order")
        if not _numbered_from_1([piece.number for piece in self.pieces]):
            raise ValueError("pieces are Videos 1, 2, 3... in order")
        if len(self.pictures) > VIDEO_API_MOST_PICTURES:
            raise ValueError(f"the video API takes at most {VIDEO_API_MOST_PICTURES} pictures")
        # Rounded so seconds that add up to the limit in floating point aren't over it.
        if round(sum(piece.seconds for piece in self.pieces), 3) > VIDEO_API_MOST_PIECE_SECONDS:
            raise ValueError(
                f"the video API takes at most {VIDEO_API_MOST_PIECE_SECONDS} s of video"
            )
        return self


def keep_sent_list(item: ProducedItem, sent: SentList) -> None:
    """Keep `sent` on the clip `item`: every clip and every try carries its own. Each of its
    pictures gets a card-size copy, for the scene's card in the chat."""
    item.sent = sent.model_dump(mode="json")
    item.save(update_fields=["sent"])
    named: dict[str, list[str]] = {}
    for picture in sent.pictures:
        named.setdefault(picture.file, []).append(f"Image {picture.image}")
    for left in sent.left_out:
        named.setdefault(left.file, []).append(f'the picture left out, "{left.label}"')
    cards.make_cards(item, named)


def sent_list_of(item: ProducedItem) -> SentList | None:
    """What the clip `item` was sent, read back through its schema; None when not recorded."""
    return None if item.sent is None else SentList.model_validate(item.sent, context=_STORED)


def add_to_library(job: Job, file: str, label: PictureLabel, source: Source) -> LibraryEntry:
    """Put a picture in `job`'s library at the next number and give back the item."""
    with transaction.atomic():
        # One item at a time per job, so two items entering at once never share a number.
        Job.objects.select_for_update().filter(pk=job.pk).first()
        last = job.library.aggregate(last=Max("number"))["last"] or 0
        item = LibraryEntry.model_validate(
            {"number": last + 1, "file": file, **label.model_dump(), "source": source.model_dump()}
        )
        LibraryItem.objects.create(job=job, **item.model_dump())
    return item


def entry(row: LibraryItem) -> LibraryEntry:
    """A library row read back through its schema."""
    return LibraryEntry.model_validate(
        {
            "number": row.number,
            "file": row.file,
            **{field: getattr(row, field) for field in PictureLabel.model_fields},
            "source": row.source,
        },
        context=_STORED,
    )


def library_of(job: Job) -> list[LibraryEntry]:
    """Every item in `job`'s library, by number."""
    return [entry(row) for row in job.library.order_by("number")]
