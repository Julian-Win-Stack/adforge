"""A stranger's face changed to the presenter's (#275): a code step that makes a version of a
library photo showing a stranger's face, with every face in it redrawn as the presenter's,
before the photo goes to the video model. A real person's face sent to it is copied into the
clip (Julian, Oct 30: "we are sending the pic with a real person's face to the video api").

`for_clip(job, item, scene=...)`: when `item` is a photo whose product photo the picker or the
face note marked as showing a stranger's face (ProductPhoto.has_face; a page photo, or a photo
the shop owner sent, as it was sent), the picture model is shown it and the presenter's
portrait as it stands, and told to redraw every face in it as the presenter's. The picture
made is checked by one ask, shown the photo and the new picture only, never the portrait (an
identity question made the model refuse): it passes when every face is drawn with features
other than the photo's, and nothing else counts (Julian, Oct 30: "as long as the face is not
there anymore"). Drawn again while it fails, at most twice (jobs.checked_pictures). One that
passes enters the library beside the photo, as a FaceSwapped item of the portrait, made once
per photo and portrait: asked again, the item is handed back and nothing is paid; a presenter
made again gets a new one the next time the photo is sent. Any other item is handed back as
it is: a cut-out, a look frame, an owner part's or a made picture, or a version of an item.

When every try fails, the picture model refuses twice, or the ad has no presenter yet, None
is given: the photo isn't sent to the video model at all, as the stranger reaching it is what
went wrong. A red notice says so, naming the scene it was asked for, once per photo and
portrait, as the next scene to ask for it gets the same.

Which pictures are sent whole, and so go through this step, is cut-and-send's. Run again, as
after a worker stopped, it pays for nothing already paid for: each picture made, and each
refusal, is taken again in turn, and each check answered from its record. An outage, of the
picture model or of the check, is raised, so the step can be run again later, rather than
failing a try."""

import logging
from collections.abc import Sequence
from typing import Literal, Self

from pydantic import BaseModel, Field, model_validator

from chat.models import Message
from gateway.gateway import UnreadableImage, call_model, edit_picture
from gateway.models import ModelCall
from gateway.types import (
    BlockedBySafetyFilter,
    Handoff,
    Image,
    UnusableReply,
)

from .checked_pictures import made_and_checked, tried_before
from .job_locks import FACE_SWAP_LOCK, LIBRARY_LOCK, one_at_a_time, one_job_at_a_time
from .library import (
    FaceSwapped,
    LibraryEntry,
    OwnerPhoto,
    PagePhoto,
    PictureLabel,
    PictureRef,
    Recoloured,
    add_to_library,
    entry,
    library_of,
)
from .models import Job, ProducedItem
from .notices import post_notice

logger = logging.getLogger(__name__)

# What picture 1 is, as the picture model and the check are told: a page photo, or a photo
# the shop owner sent.
PAGE_PHOTO = "a photo from a shop's page"
OWNER_PHOTO = "a product photo"

# What the picture model is told, shown the photo then the portrait. Tested Oct 30 on round
# 7's six photos with a stranger's face: every one passed (thread "Stop copying real people
# from photos"). "EVERY face": a photo of a before and an after kept one stranger otherwise.
SWAP_PROMPT = (
    "Picture 1 is {photo}. Picture 2 is a portrait of the person who presents the ad. "
    "Redraw EVERY face in picture 1 (there may be more than one, for example a before and an "
    "after) as the face of the person in picture 2: their face shape, eyes, brows, nose, lips, "
    "skin tone and hair. No face may keep any feature of the original person. Keep the rest "
    "of picture 1: the product with its labels, the pose, the hands and the background."
)

# What the check is told, tested with SWAP_PROMPT. It never asks who anyone is: asked whether
# the face is the presenter's, the model refused.
FACE_CHECK = """\
You check a picture that an AI picture model edited. You are shown two pictures: picture 1, \
{photo} with a person in it, and the new picture, where each face was to be redrawn as a \
different face. You do not identify anyone. Compare only how the faces are drawn.
1. Look before you judge. For each face in the new picture, write which visible features \
differ from the matching face in picture 1: face shape, eyes, brows, nose, lips, skin tone, \
hairline and hair.
2. Then judge. Decide "pass" when every face in the new picture is drawn with clearly \
different features from picture 1. Nothing else counts: changes to the body, clothes, pose, \
product, background or framing never fail it. Decide "fail" only when a face is drawn with \
the same features as in picture 1."""

# Said beside the two pictures.
PICTURES_SHOWN = "Picture 1 is the photo, and the new picture is last."


class FaceCheckHandoff(Handoff):
    """Which picture is which: the check is shown the two, and nothing else."""

    pictures: str = PICTURES_SHOWN


# The check's answer, filled in from the top: it writes down what it sees before it
# decides. A comment, not a docstring: a docstring would be sent to the model in the
# answer's schema.
class FaceSwapVerdict(BaseModel):
    features: str = Field(
        description="For each face in the new picture, which visible features differ from "
        "the matching face in picture 1."
    )
    verdict: Literal["pass", "fail"]
    problem: str | None = Field(
        description="When it fails: which face kept which features, in one sentence. Null "
        "when it passes."
    )

    @model_validator(mode="after")
    def _a_failure_says_why(self) -> Self:
        if self.verdict == "fail" and not (self.problem or "").strip():
            raise ValueError("A picture that fails must say what is wrong.")
        return self


# Why the check couldn't be made, in plain words, by what went wrong: each fails the
# picture, as asking again would go the same way. An outage (OutsideServiceDown,
# AnswerTookTooLong) is raised instead, as the picture model's is.
_WHY_UNCHECKED: dict[type[Exception], str] = {
    UnusableReply: "the check's answer couldn't be used",
    UnreadableImage: "a picture couldn't be read for the check",
    BlockedBySafetyFilter: "the check's safety filter refused the pictures",
}


def for_clip(job: Job, item: LibraryEntry, *, scene: int) -> LibraryEntry | None:
    """What goes to a clip of scene `scene` for the library item `item`, sent whole: its
    FaceSwapped version of the presenter's portrait as it stands, made now when it has none,
    when it shows a stranger's face; else `item` itself. None, which a red notice says the
    first time, when the face can't be changed: the photo isn't sent."""
    what = _photo_with_a_face(job, item)
    if what is None:
        return item
    portrait = _portrait(job)
    if portrait is None:
        _say_no_presenter(job, item, scene)
        return None
    # Only the same photo waits: the ad's other swaps go on.
    with one_at_a_time(FACE_SWAP_LOCK, f"{job.pk}:{item.number}"):
        source = FaceSwapped(of=item.number, portrait=portrait.file)
        if (made := _made_before(job, source)) is not None:
            return made
        prompt = SWAP_PROMPT.format(photo=what)
        to_send = [item.file, portrait.file]
        tried = tried_before(job, "swap_face", prompt, to_send)

        def make(tries: int) -> str:
            if tries < len(tried):
                before = tried[tries]
                if isinstance(before, BlockedBySafetyFilter):
                    raise before
                return before
            return edit_picture(
                job=job, purpose="swap_face", prompt=prompt, pictures=to_send, keep_shape=True
            )

        checked = made_and_checked(make, lambda file: _check(job, what, item.file, file))
        if checked.file is None:
            _say_it_failed(job, item, portrait, scene, checked.problems)
            return None
        label = PictureLabel.model_validate(item.model_dump(include=set(PictureLabel.model_fields)))
        with one_job_at_a_time(LIBRARY_LOCK, job):
            return add_to_library(job, checked.file, label, source)


def none_shows_a_stranger(job: Job, pictures: Sequence[PictureRef]) -> bool:
    """Whether `pictures`, sent before, are free of strangers as the ad stands: none is a
    photo with a stranger's face sent whole, nor a swap (or a paint of one) made from a
    portrait that isn't the presenter's as they stand. A clip sent them again, as a note's
    clip is, is sent them only then: else cut-and-send cuts the clip's pictures again."""
    library = {item.number: item for item in library_of(job)}
    portrait = _portrait(job)
    for picture in pictures:
        item = library.get(picture.library_number or 0)
        while item is not None and isinstance(item.source, Recoloured):
            item = library.get(item.source.of)
        if item is None:
            continue
        if isinstance(item.source, FaceSwapped):
            if portrait is None or item.source.portrait != portrait.file:
                return False
        elif _photo_with_a_face(job, item) is not None:
            return False
    return True


def _portrait(job: Job) -> ProducedItem | None:
    """The presenter's portrait as it stands, None before the presenter is made."""
    return job.produced.filter(kind=ProducedItem.Kind.PORTRAIT).order_by("version").last()


def _photo_with_a_face(job: Job, item: LibraryEntry) -> str | None:
    """What kind of photo `item` is (PAGE_PHOTO or OWNER_PHOTO) when it is a photo whose
    product photo shows a stranger's face; None for any other item."""
    match item.source:
        case PagePhoto(photo=position):
            photos, what = job.photos.filter(position=position), PAGE_PHOTO
        case OwnerPhoto(original=original) if item.file == original:
            photos, what = job.photos.filter(file=original), OWNER_PHOTO
        case _:
            return None
    return what if photos.filter(has_face=True).exists() else None


def _made_before(job: Job, source: FaceSwapped) -> LibraryEntry | None:
    """The item of `job`'s library made from `source`'s photo and portrait, if any."""
    row = job.library.filter(source=source.model_dump()).order_by("number").first()
    return None if row is None else entry(row)


def _check(job: Job, what: str, photo: str, made: str) -> str | None:
    """What is wrong with the picture `made` from `photo`, a photo of kind `what`; None when
    every face in it is redrawn. A check that can't be made fails it, saying why: a picture
    nobody looked at is never sent as one without the stranger. Asked again, as by the next
    scene to send the photo, a check that couldn't be made is answered from its record, so a
    photo the chat was told isn't sent never is (a check that answered is, by pay_once)."""
    if (why := _unchecked_before(job, photo, made)) is not None:
        return why
    try:
        verdict = call_model(
            job=job,
            purpose="check_face_swap",
            instructions=FACE_CHECK.format(photo=what),
            handoff=FaceCheckHandoff(),
            output=FaceSwapVerdict,
            images=[Image("Picture 1, the photo:", photo), Image("The new picture:", made)],
            pay_once=True,
        )
    except tuple(_WHY_UNCHECKED) as error:
        logger.warning("The check of the face-swapped picture %s couldn't be made: %s", made, error)
        return next(text for kind, text in _WHY_UNCHECKED.items() if isinstance(error, kind))
    return None if verdict.verdict == "pass" else verdict.problem


def _unchecked_before(job: Job, photo: str, made: str) -> str | None:
    """Why the check of `made` from `photo` couldn't be made before, if it was asked and never
    answered: the reason _WHY_UNCHECKED gives for what it raised. None when it answered, or
    was never asked, or an outage stopped it."""
    calls = job.model_calls.filter(
        purpose="check_face_swap", handoff=FaceCheckHandoff().model_dump(mode="json")
    )
    shown = [
        call
        for call in calls.order_by("created_at", "id")
        if [image["key"] for image in call.images] == [photo, made]
    ]
    if any(call.outcome == ModelCall.Outcome.SUCCEEDED for call in shown):
        return None
    for call in shown:
        raised = call.error.split(":", 1)[0]
        for kind, why in _WHY_UNCHECKED.items():
            if raised in {each.__name__ for each in (kind, *_subclasses(kind))}:
                return why
    return None


def _subclasses(kind: type[Exception]) -> list[type[Exception]]:
    found = []
    for each in kind.__subclasses__():
        found += [each, *_subclasses(each)]
    return found


def _say_it_failed(
    job: Job, item: LibraryEntry, portrait: ProducedItem, scene: int, problems: list[str]
) -> None:
    """The red notice for a swap every try failed, the first time it fails for this portrait:
    what failed, that the photo isn't sent, and why each try failed."""
    opening = f"{_opening(item)}, and it couldn't be changed to {_presenters_face(portrait)}"
    found = " ".join(f"Try {tries}: {problem}" for tries, problem in enumerate(problems, 1))
    _say_once(
        job,
        opening,
        f"{opening}: it failed every time it was tried, so it isn't sent to the video model for "
        f"scene {scene} or any other scene. Clips made without it may not show what it shows. "
        f"{found}",
    )


def _say_no_presenter(job: Job, item: LibraryEntry, scene: int) -> None:
    """The red notice for a photo with a stranger's face asked for before the presenter is
    made, the first time."""
    opening = f"{_opening(item)}, and the ad has no presenter yet to change it to"
    _say_once(
        job,
        opening,
        f"{opening}, so it isn't sent to the video model for scene {scene} or any other scene "
        "until the presenter is made. Clips made without it may not show what it shows.",
    )


def _opening(item: LibraryEntry) -> str:
    return (
        f'Picture {item.number} of the ad\'s picture library ("{item.label}") shows a '
        "stranger's face"
    )


def _presenters_face(portrait: ProducedItem) -> str:
    """The presenter's face, as a notice names it: the presenter made again names its version,
    so a swap failing for a new presenter is said again."""
    if portrait.version == 1:
        return "the presenter's face"
    return f"the face of the presenter as made again (version {portrait.version})"


def _say_once(job: Job, opening: str, text: str) -> None:
    """Say `text` in red, with its warning line, unless the job was told something starting
    with `opening`: a later scene asking for the same photo isn't told again."""
    job.refresh_from_db(fields=["warnings"])
    if any(warning["text"].startswith(opening) for warning in job.warnings):
        return
    logger.warning("Job %s: %s", job.pk, text)
    post_notice(job, text, Message.Level.PROBLEM)
