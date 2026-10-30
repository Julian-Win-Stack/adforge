"""The hard list (spec #247 point 13): the only refusals of the shop owner's or the producer's
wish that live in code. Each is a tool answer, a can't with the reason, never a stop:

- a new product mid-ad: the read-page tool refuses a second product's page in a session;
- a clip of an invented part (the owner part) before the shop owner's yes on its picture;
- the video API's limits: code cuts what a clip is sent to them (jobs.cut_and_send), and the
  clip's answer names what was left out and why.

Nothing else is a code refusal of a wish. A new guard is added only after a graded failure,
as a tool answer with a reason, and lives here only if it refuses a wish."""

from collections.abc import Sequence
from typing import NoReturn
from urllib.parse import parse_qsl, urlencode, urlsplit

from django.conf import settings

from adforge.retry import OutsideServiceDown
from gateway.types import MOST_PIECE_SECONDS
from jobs import owner_parts, page, work
from jobs.cut_and_send import DRAWN_NAMES, TIER_NAMES, Excluded, Sending
from jobs.library import VIDEO_API_MOST_PICTURES, LeftOut, StrangerLeftOut, library_of
from jobs.models import Job, Scene

from .loop import Refused

# What the producer is told to tell the shop owner of another product's page, and the
# chat's button that starts a new ad (frontend App, "New session").
ANOTHER_PRODUCT = "start a new ad for that"
NEW_AD_BUTTON = "New session"

# Query parameters that say where a visitor came from, not which product a page is for.
_TRACKING_PREFIXES = ("utm_",)
_TRACKING = frozenset({"fbclid", "gclid", "msclkid", "srsltid", "ref", "_pos", "_sid", "_ss"})


def refuse_part_picture_elsewhere(scene: Scene, numbers: list[int]) -> None:
    """Refuse naming any of the library items `numbers` for `scene` when it is an owner part's
    picture, or a picture made from one, and `scene` doesn't hold that part: such a picture
    goes only to the scenes holding the part, whose clip waits for the shop owner's yes."""
    library = {item.number: item for item in library_of(scene.job)}
    for number in numbers:
        part = owner_parts.part_not_held(scene, library[number], library)
        if part is not None:
            raise Refused(
                f"library item {number} shows an owner part (pictured in library item "
                f"{part.number}: {part.label}), and scene {scene.number} doesn't hold it. A "
                "part's picture, or one made from it, goes only to the scenes holding the "
                "part, after the shop owner's yes: name it there."
            )


def refuse_another_product(job: Job, link: str) -> None:
    """Refuse `link` when the job has a product page already and `link` is another
    product's: one ad never mixes two products. Asked before anything is read, so the job's
    page is never replaced by another product's. A link is this ad's product when it is,
    or its page says it is (where it answers from, or the address it names as its own), the
    link first given, where that page answered from, or the address it named as its own."""
    known = [job.product_url, job.page_final_url, job.page_canonical_url]
    if _among(link, known):
        return
    try:
        theirs = work.page_addresses(link)
    except (page.PageUnreadable, OutsideServiceDown) as error:
        raise Refused(
            f"this ad's page, {job.product_url}, has already been read, and {link} couldn't be "
            f"opened to tell whether it is the same product ({error}). Ask the shop owner "
            "whether it is: an ad is for one product."
        ) from error
    if any(_among(address, known) for address in theirs):
        return
    raise Refused(
        f"this ad is for the product at {job.product_url}, whose page has already been read, "
        f"and {link} is another product's page. One ad is for one product: tell the shop "
        f'owner "{ANOTHER_PRODUCT}" in plain words, with the chat\'s "{NEW_AD_BUTTON}" '
        "button, and go on with this ad."
    )


def _among(link: str, known: list[str]) -> bool:
    return any(same_product(link, each) for each in known if each)


def same_product(link: str, other: str) -> bool:
    """Whether two links are to the same product's page: the same address, whatever its
    scheme, a leading "www.", a trailing slash, a place on the page (#...) or the
    parameters that only track where a visitor came from. Any other difference, a parameter
    that picks the product among them, is another product."""
    return _product_address(link) == _product_address(other)


def _product_address(link: str) -> tuple[str, str, str]:
    parts = urlsplit(link.strip())
    host = (parts.hostname or "").lower().removeprefix("www.")
    if parts.port is not None:
        host = f"{host}:{parts.port}"
    query = sorted(
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key.lower() not in _TRACKING and not key.lower().startswith(_TRACKING_PREFIXES)
    )
    return host, parts.path.rstrip("/") or "/", urlencode(query)


def waiting_owner_part(scene: Scene) -> owner_parts.OwnerPart | None:
    """The scene's owner part, the part no photo shows, while it waits for the shop owner's
    yes on its picture (spec #210 point 20): no clip of the scene is made until then. None
    when the scene has none, or the yes is given."""
    part = owner_parts.owner_part(scene)
    return None if part is None or owner_parts.is_approved(scene) else part


def refuse_clip_before_the_owners_yes(
    scene: Scene, part: owner_parts.OwnerPart, picture: str
) -> NoReturn:
    """Refuse a clip of `scene` while `part` waits for the shop owner's yes on its
    `picture`, named as the library names it."""
    raise Refused(
        f"scene {scene.number} shows {part.what}, which no photo shows, and its clip isn't "
        f"made until the shop owner says yes to the part's picture ({picture}). They were "
        "shown it in the chat: ask them, in plain words, to say yes if it looks right, or say "
        "what to change, or send a photo of it, and wait for their answer."
    )


def cut_answer(scene: int, sending: Sending) -> str:
    """What the producer is told of the cut of scene `scene`'s clip to the video API's
    limits: each picture and piece of video left out, and why, each piece cut short, each
    item the scene's exclude list kept out, and each photo kept out for a stranger's face.
    Blank when nothing was cut. Made part of the clip's answer, so the producer can choose
    which pictures matter for the scene, exclude ones that don't, or make one combined
    picture."""
    told = []
    if sending.left_out:
        told.append(left_out_answer(scene, sending.left_out))
    if sending.excluded:
        told.append(excluded_answer(scene, sending.excluded))
    if sending.strangers_left_out:
        told.append(strangers_answer(scene, sending.strangers_left_out))
    for left in sending.pieces_left_out:
        told.append(
            f"The piece of video {left.video}, moment {left.moment} ({left.start:g} to "
            f"{left.end:g} s), was left out of scene {scene}'s clip: {left.reason}."
        )
    for piece in sending.pieces:
        if piece.trim in ("moment_over_limit", "pieces_over_limit"):
            told.append(
                f"Video {piece.number} of scene {scene}'s clip (video {piece.video}, moment "
                f"{piece.moment}) was cut to {piece.start:g} to {piece.end:g} s of its "
                f"{piece.moment_start:g} to {piece.moment_end:g} s: the video model takes at "
                f"most {MOST_PIECE_SECONDS:g} s of video over up to {_pieces()}."
            )
    return " ".join(told)


def excluded_answer(scene: int, excluded: Sequence[Excluded]) -> str:
    """The items scene `scene`'s exclude list kept out of its clip, each by its label,
    number and the tier that would have taken it."""
    named = "; ".join(
        f'"{item.label}" (library item {item.number}, {TIER_NAMES[item.tier]})' for item in excluded
    )
    return f"Not sent, as excluded for scene {scene}: {named}."


def strangers_answer(scene: int, strangers: Sequence[StrangerLeftOut]) -> str:
    """The photos kept out of scene `scene`'s clip as each shows a stranger's face that
    couldn't be changed to the presenter's (jobs.face_swap), each by its label, number and
    the tier that took it."""
    named = "; ".join(_named(picture) for picture in strangers)
    return (
        f"Not sent to scene {scene}'s clip, as each shows a stranger's face that couldn't be "
        f"changed to the presenter's (a notice records why): {named}."
    )


def left_out_answer(scene: int, left_out: Sequence[LeftOut]) -> str:
    """The pictures the video API's cap leaves out of scene `scene`'s clip, and why."""
    many = len(left_out) != 1
    return (
        f"The video model takes at most {VIDEO_API_MOST_PICTURES} pictures, and the "
        f"{VIDEO_API_MOST_PICTURES} that come first in the send order filled it, so "
        f"{len(left_out)} picture{'s were' if many else ' was'} left out of scene "
        f"{scene}'s clip: {'; '.join(_named(picture) for picture in left_out)}."
    )


def _pieces() -> str:
    most: int = settings.BROLL_MOST_PIECES
    return f"{most} piece{'s' if most != 1 else ''}"


def _named(picture: LeftOut | StrangerLeftOut) -> str:
    """A picture not sent by its label, what it is, and its tier: why it was taken, which,
    for one the cap left out, put it after the pictures that filled the cap."""
    what = (
        f"library item {picture.library_number}"
        if picture.library_number is not None
        else DRAWN_NAMES[picture.drawn]
        if picture.drawn is not None
        else "a picture"
    )
    return f'"{picture.label}" ({what}, taken as {TIER_NAMES[picture.tier]})'
