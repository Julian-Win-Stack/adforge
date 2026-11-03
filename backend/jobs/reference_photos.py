"""Naming and drawing clean pictures of the shop owner's own things.

Shared by retained talking-picture and library paths. B-roll scene agents pick
library photos for drawing; these pictures are never direct video references.
"""

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
