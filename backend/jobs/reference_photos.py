"""The shop owner's photos on notes (Reference photo, CONTEXT.md; Julian 2026-10-15, build
spec handoffs/owner-photos-build-spec.md). A fix names one thing to take from each photo, and
the owner confirms it. Code draws a clean picture of only that thing from the photo, and
sends it first, labelled to win wherever it disagrees with another picture (option C, 16:01).
Raw photos never go to the video model (locked rule 1, 19:38).

Here: how a thing is named, and the words code adds to the prompts. The pictures are drawn in
jobs.work."""

import logging
import re

logger = logging.getLogger(__name__)


_ARTICLE = re.compile(r"^(the|a|an)(\s+|$)", re.IGNORECASE)


def name_of(thing: str) -> str:
    """A thing's name as it is kept: its spacing tidied and any "the", "a" or "an" in front
    dropped, so code can say "the shop owner's own black toilet"."""
    return _ARTICLE.sub("", " ".join(thing.split()))


def same_thing(one: str, other: str) -> bool:
    """Whether two names name the same thing, whatever their spacing, case or article."""
    return name_of(one).casefold() == name_of(other).casefold()


class ReferencePhotoUnusable(Exception):
    """The clean picture of a thing couldn't be drawn from the shop owner's photo: it kept
    failing its check, or the picture model refused it. Nothing more is made for the scene:
    the shop owner is asked for another photo (D8). Says why, for the producer."""

    def __init__(self, why: str, *, thing: str) -> None:
        super().__init__(why)
        self.thing = thing


def clean_picture_prompt(thing: str) -> str:
    """The picture model's prompt for the clean picture of only `thing` from the shop owner's
    photo. It always says never to copy a face, whoever is in the photo (Julian 17:45-17:46;
    a hand may stay where it holds the thing), and never to draw a side the photo doesn't
    show (locked rule 3; design thread 22:27)."""
    return clean_photo_of(f"the {thing}")


def clean_photo_of(what: str) -> str:
    """The picture model's prompt for a cut-out of only `what` ("the black mug") from the
    photo it is given, as the clean picture of an owner's thing is drawn."""
    return (
        f"A clean photo of only {what} in this photo, exactly as it looks: the same "
        "shape, colours, materials, print and every part. Nothing else from the photo: no "
        "other object and no background. Never copy any person's face. Seen from the side "
        "the photo shows it from, never from a side the photo doesn't show, on a plain light "
        "grey background, in even, soft light."
    )


def during_clip_rules(things: list[str]) -> str:
    """Added to what a model writing a B-roll clip's video prompt is told when the shop
    owner's things only show during the clip: the video model is shown each one's clean
    picture after the starting picture, and code tells it so."""
    listed = _listed([f"their {thing}" for thing in things])
    return (
        f"The shop owner sent photos of {listed}, which only show once the clip's action "
        "starts. Code shows the video model a clean picture of each, after the starting "
        "picture, and tells it to follow them on how each looks. Name each by what it is, "
        "never by how it looks, and never write anything about its look that could disagree "
        "with the shop owner's picture."
    )


def _listed(items: list[str]) -> str:
    """ "a", "a and b", or "a, b and c"."""
    return items[0] if len(items) == 1 else f"{', '.join(items[:-1])} and {items[-1]}"
