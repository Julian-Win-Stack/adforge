"""The shop owner's photos on notes (Reference photo, CONTEXT.md; Julian 2026-10-15, build
spec handoffs/owner-photos-build-spec.md). A fix names one thing to take from each photo, and
the owner confirms it. Code draws a clean picture of only that thing from the photo, and
sends it first, labelled to win wherever it disagrees with another picture (option C, 16:01).
Where the thing is in the scene's starting picture, or belongs in it from the start, the
starting picture is drawn again with it; where it only shows during the clip, the clip is made
in reference mode: its starting picture first, told the clip starts like it, then the clean
pictures. A model looks at each scene's starting picture to say which (the route call, 20:00),
and the shop owner isn't told. Raw photos never go to the video model (locked rule 1, 19:38).

Here: the route call, how a thing is named, and the words code adds to the prompts. The
pictures are drawn in jobs.work."""

import logging
import re
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from adforge.retry import OutsideServiceDown
from gateway.gateway import call_model
from gateway.types import BlockedBySafetyFilter, Handoff, Image, UnusableReply

from .models import Job

logger = logging.getLogger(__name__)

# The most photos one note's fix, or one scene, takes things from (Julian 17:43 "up to 3";
# D4: a fourth on a scene makes the producer ask which to drop).
MOST_PHOTOS = 3

ROUTE_INSTRUCTIONS = """\
You help make a short video ad for a shop. Each scene of the ad is a short video clip that \
starts from a still starting picture. The shop owner sent a photo of one of their own things, \
named for you, to change how it looks in the ad. You are shown the scene's starting picture \
first and the shop owner's photo second, and told what the scene shows and, if the shop \
owner asked for a change to this scene, the change.

Work in three steps.
1. Look at the shop owner's photo. Write what it shows of the named thing. Then say whether \
the thing is clear enough in it to copy its colour, shape and details. An ordinary phone \
photo is clear enough, even a bit soft, dark or taken from an angle; answer no only when you \
can't really see the thing: too blurry, too dark, too small or mostly hidden. When it isn't, \
say why in a few words.
2. Look before you decide. Write what the starting picture shows of that thing, or of \
anything that could be it in a different look: where it is and how it looks, or that \
nothing like it is there.
3. Then decide where the named thing goes in this scene:
- "in_the_picture": the starting picture shows that thing, or something in its place that \
could be it, even if it looks different from the photo.
- "belongs_from_the_start": the starting picture doesn't show it, but the change asks for it \
to be in the scene from the first moment, such as an object added to the place.
- "appears_during_the_clip": it only shows once the action starts, such as a gel squeezed out \
of a tube, foam, or something a hand brings in.
Say in one sentence why."""

Where = Literal["in_the_picture", "belongs_from_the_start", "appears_during_the_clip"]


class RouteHandoff(Handoff):
    """The thing the shop owner's photo is taken for, what the scene shows, and the change
    they asked for this scene: null when they didn't name it."""

    thing: str
    scene_shows: str
    change: str | None
    pictures: str = "The starting picture is first; the shop owner's photo is second."


# Filled in from the top, so it looks at both pictures before it decides (vision-judges.md,
# rule 1). A comment, not a docstring: a docstring would be sent in the answer's schema.
class RouteAnswer(BaseModel):
    photo_shows: str = Field(description="What the shop owner's photo shows of the thing.")
    clear_enough: bool = Field(
        description="Whether the thing is clear enough in the photo to copy its look."
    )
    unclear_why: str | None = Field(
        description="When it isn't clear enough: why, in a few words. Null when it is."
    )
    picture_shows: str = Field(
        description="What the starting picture shows of the thing, or that it isn't there."
    )
    where: Where
    why: str = Field(description="One sentence saying why.")


def where_it_goes(
    job: Job, *, picture: str, photo: str, thing: str, shows: str, change: str | None
) -> RouteAnswer | None:
    """Where `thing`, taken from the shop owner's photo at `photo`, goes in the scene whose
    starting picture is at `picture` and which shows `shows`: asked once of a model shown both,
    and answered from its record when asked the same again. `change` is the fix the shop owner
    asked for the scene, or None for a scene they didn't name. None when no answer could be
    had."""
    try:
        return call_model(
            job=job,
            purpose="route_reference_photo",
            instructions=ROUTE_INSTRUCTIONS,
            handoff=RouteHandoff(thing=thing, scene_shows=shows, change=change),
            output=RouteAnswer,
            images=[
                Image(label="The starting picture:", key=picture),
                Image(label="The shop owner's photo:", key=photo),
            ],
            pay_once=True,
        )
    # ValidationError: an answer kept from before the answer's form changed can't be read.
    except (UnusableReply, OutsideServiceDown, BlockedBySafetyFilter, ValidationError) as error:
        logger.warning("Where the %s goes couldn't be had: %s", thing, error)
        return None


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


def picture_job(thing: str) -> str:
    """The job of the clean picture of `thing`, sent first to the picture model."""
    return (
        f"the shop owner's own {thing}: wherever it disagrees with another picture, follow this one"
    )


def picture_label(things: list[str]) -> str:
    """Put by code before the starting picture's prompt, so the picture model always gets it:
    a sentence for each clean picture, sent first, in order (option C, Julian 16:01), then
    which picture is the product photo, which the prompt names by no number."""
    return " ".join(
        [
            *(
                f"Image {number} is the shop owner's own {thing}: wherever it disagrees with "
                f"another picture, follow Image {number}."
                for number, thing in enumerate(things, 1)
            ),
            f"Image {len(things) + 1} is the product photo.",
        ]
    )


def writer_rules(in_picture: list[str], during_clip: list[str], *, picks_photo: bool) -> str:
    """Added to what the model writing a B-roll scene's prompts is told when the scene uses
    the shop owner's photos: those drawn into the starting picture, sent first, and those
    that only show during the clip, whose look the video model gets from their clean
    pictures (locked rule 2, changed by Julian 19:38). Told how to answer "photo" only when
    it `picks_photo`: with one photo in the ad's colour, code fills it in."""
    rules = []
    if in_picture:
        each = "; ".join(
            f"Image {number}: their {thing}" for number, thing in enumerate(in_picture, 1)
        )
        answer = (
            'Answer "photo" with its Photo number, as "colour_photos" gives it. '
            if picks_photo
            else ""
        )
        rules.append(
            "The shop owner sent photos of their own things. Code sends a clean picture of only "
            "each thing, drawn from their photo, to the picture model first, before every other "
            f'picture ({each}). Wherever the rules above say Image 1, write "the product '
            'photo" instead, never an Image number: code tells the picture model which picture '
            f"it is. {answer}You are shown them first, before the product photos. They are the "
            "shop owner's "
            'own, so wherever one disagrees with another picture or with "before_picture" '
            "about how that thing looks, it is right. Never write an order that disagrees with "
            "them: say to take each thing's look from its picture. Code puts a sentence saying "
            "they win before your picture prompt."
        )
    if during_clip:
        rules.append(during_clip_rules(during_clip))
    return "\n".join(rules)


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


def reference_mode_label(things: list[str]) -> str:
    """Put by code before a clip's video prompt in reference mode: the starting picture is
    Image 1 and the clip starts like it (Julian 05:12, "this is how the thing should start"),
    then a sentence for each clean picture of the shop owner's things, in order."""
    return " ".join(
        [
            "Image 1 is how the clip starts: the clip starts exactly like Image 1.",
            *(
                f"Image {number} is the shop owner's own {thing}: wherever it disagrees with "
                f"another picture, follow Image {number}."
                for number, thing in enumerate(things, 2)
            ),
        ]
    )


def _listed(items: list[str]) -> str:
    """ "a", "a and b", or "a, b and c"."""
    return items[0] if len(items) == 1 else f"{', '.join(items[:-1])} and {items[-1]}"
