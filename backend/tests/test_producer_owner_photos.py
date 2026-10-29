"""The shop owner's photos on notes (Reference photo, CONTEXT.md): a fix plan names one thing
to take from each photo and, per scene, whether it is in the starting picture now and whether
more of it shows during the action (fix redesign, decisions 5, 14 and 21). After the yes, code
draws a clean picture of only that thing from the photo, and either draws the scene's starting
picture again with it, sent first and labelled to win, or makes its clip in reference mode, the
starting picture first and the clean picture after (Julian 2026-10-15,
handoffs/owner-photos-build-spec.md). The first ad is made through the chat; the fixes are
plans driven by tests/fixing.py, or through the chat with a scripted decider. The models are
faked at the gateway; the clips are real tiny videos and ffmpeg runs for real."""

from collections.abc import Callable
from typing import Any

import pytest

from chat.models import Message
from gateway.fake import FakeModel, turn
from gateway.models import ModelCall
from jobs.models import (
    Job,
    ProducedItem,
    Scene,
    SceneStep,
)

from .conftest import (
    CUT_OUT_OK,
    FACTS_OK,
    LABELLED,
    NO_CHOICES,
    PLAN,
    HeldSteps,
    broll,
    picture,
)
from .test_producer_broll import (
    BROLL_CHOICE,
    LINE_2,
    SHOWS,
    TALKING_CHOICE,
    calling,
    instructed,  # noqa: F401 (a fixture)
    run,
)
from .test_recolour import RECOLOUR_OK

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(autouse=True)
def recolours_pass(fake_model: FakeModel) -> None:
    """Every photo is labelled the clip's colour, the shop owner's things too, so none is
    recoloured; one that is passes its check."""
    fake_model.answer_unscripted("label_photo", lambda _: {**LABELLED, "colour": "sage-green"})
    fake_model.answer_unscripted("check_recolour", lambda _: RECOLOUR_OK)


# The cut-out check failing a clean picture.
CUT_OUT_FAILS: dict[str, Any] = {
    **CUT_OUT_OK,
    "verdict": "fail",
    "problem": "The handle is fused into the side of the mug.",
}

Say = Callable[..., None]

SHOWS_3 = "The mug on a kitchen shelf beside a black teapot."
# Eight words: four seconds at the fake voice's two words a second, the least a B-roll line
# may take.
LINE_3 = "Yours for $24.00, made by hand in Kiln."

# The mug plan with scenes 2 and 3 both showing the mug.
TWO_BROLL_PLAN: dict[str, Any] = {
    **PLAN,
    "plan": {
        **PLAN["plan"],
        "scenes": [
            PLAN["plan"]["scenes"][0],
            broll({**PLAN["plan"]["scenes"][1], "line": LINE_2, "shows": SHOWS}),
            broll({**PLAN["plan"]["scenes"][2], "line": LINE_3, "shows": SHOWS_3}),
        ],
    },
}

OWNER_MUG = ("my-mug.png", picture(60, 80, (20, 20, 20)))
OWNER_TEA = ("my-tea.png", picture(60, 80, (180, 90, 20)))

MUG_NOTE = "Our mug is black, make it look like this one."
MUG_FIX = "In scene 2, the mug looks like the shop owner's black mug."
TEA_NOTE = "The tea should look like this, it's our own blend."
TEA_FIX = "In scene 2, the tea poured in looks like the shop owner's tea."
BRIGHTER = "In scene 2, the workshop is brighter."
LINE = "Black stoneware, holds 350 ml, dishwasher safe."

WHERE_SAYS = {
    "photo_shows": "A black stoneware mug.",
    "clear_enough": True,
    "unclear_why": None,
    "picture_shows": "A sage green mug on a workbench.",
    "why": "The mug is in the picture.",
}


BLURRED = "The photo is too blurry to see the mug's shape."


def where(answer: str) -> dict[str, Any]:
    """The route call answering `answer`; "unclear" when the thing can't be seen in the
    photo, though it is in the starting picture."""
    if answer == "unclear":
        return {
            **WHERE_SAYS,
            "clear_enough": False,
            "unclear_why": BLURRED,
            "where": "in_the_picture",
        }
    return {**WHERE_SAYS, "where": answer}


@pytest.fixture
def checked(fake_model: FakeModel, page_read: str, say: Say) -> None:
    """A chat whose ad is planned with scenes 2 and 3 showing the mug, has its person, and
    whose three scenes passed the fact check."""
    fake_model.respond("produce", turn(calls=[("plan_ad", {})]), turn(says="Here's the plan."))
    fake_model.respond("plan_ad", TWO_BROLL_PLAN)
    say("Plan it")
    fake_model.respond(
        "produce",
        turn(calls=[("create_person", {})]),
        turn(calls=[("run_planning_checks", NO_CHOICES)]),
        turn(says="The script is checked."),
    )
    fake_model.respond("fact_check", FACTS_OK)
    say("Make the person and check the script")
    assert Scene.objects.get(number=3).shows == SHOWS_3


def made_ready(fake_model: FakeModel, steps: HeldSteps, say: Say) -> None:
    calling(
        fake_model,
        say,
        *[("make_starting_picture", {"scene": scene, "note": None}) for scene in (1, 2, 3)],
        *[("make_line_audio", {"scene": scene}) for scene in (1, 2, 3)],
    )
    fake_model.respond("choose_starting_picture", TALKING_CHOICE)
    fake_model.respond("choose_broll_picture", BROLL_CHOICE, BROLL_CHOICE)
    run(fake_model, steps)
    calling(fake_model, say, *[("transcribe_line_audio", {"scene": scene}) for scene in (1, 2, 3)])
    run(fake_model, steps)


@pytest.fixture
def shown(fake_model: FakeModel, checked: None, steps: HeldSteps, say: Say) -> ProducedItem:
    """The first ad, made and shown through the chat."""
    calling(fake_model, say, ("create_music", {"mood": "light upbeat lo-fi"}))
    made_ready(fake_model, steps, say)
    calling(fake_model, say, *[("make_clip", {"scene": scene}) for scene in (1, 2, 3)])
    run(fake_model, steps)
    calling(fake_model, say, ("assemble_ad", {}))
    return Job.objects.get().produced.get(kind="finished_ad")


def photos_sent(fake_model: FakeModel, say: Say, *photos: tuple[str, bytes]) -> None:
    """The shop owner sends `photos` through the chat, numbered by it from the photos before;
    the decider sorts the message as no fix note (unscripted), so the producer answers it."""
    fake_model.respond("produce", turn(says="Thanks, I have your photos."))
    say("Here are our photos.", *photos)


def owner_file(number: int) -> str:
    """The shop owner's photo `number`, as kept in the file store."""
    from chat.messages import owner_photos
    from chat.models import Session

    return owner_photos(Session.objects.get())[number - 1].file


def clean_pictures() -> list[str]:
    """Each clean picture drawn of a shop owner's thing, oldest first."""
    return [
        call.output["file"]
        for call in ModelCall.objects.filter(purpose="make_reference_photo_picture").order_by("id")
        if call.output
    ]


def latest(kind: str, scene: int = 2) -> ProducedItem:
    return ProducedItem.objects.filter(kind=kind, scene__number=scene).latest("version")


def last_step(kind: str, scene: int = 2) -> SceneStep:
    step = SceneStep.objects.filter(kind=kind, scene__number=scene).last()
    assert step is not None
    return step


def in_use(scene: int = 2) -> list[tuple[str, str]]:
    return list(Scene.objects.get(number=scene).photos_in_use().values_list("thing", "use"))


def problems() -> list[str]:
    """What the chat told the shop owner went wrong."""
    return list(Message.objects.filter(level="problem").values_list("text", flat=True))


# --- Decision 5's fallback: the route call in place of the decider's answers -------------------


# --- The photo numbers of the chat ------------------------------------------------------------


# --- A new starting picture with the photo -----------------------------------------------------


# --- Reference mode ------------------------------------------------------------------------------


REWRITE: dict[str, Any] = {
    "motion_prompt": "Tea pours from the teapot into the mug.",
    "motion_prompt_reason": "The note is about the tea.",
}


# --- Several photos on one note ------------------------------------------------------------------


MUG_AND_TEA = "In scene 2, the mug and the tea look like the shop owner's."


# --- The photo stays with the scene (D1) --------------------------------------------------------


# --- Going back, a new photo of the same thing ---------------------------------------------------


# --- A photo that can't be drawn clean (D8) -----------------------------------------------------


# --- The clean picture is drawn once -----------------------------------------------------------


# --- The job's library (spec #210 point 1) ----------------------------------------------------
