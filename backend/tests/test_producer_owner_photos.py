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
from decimal import Decimal
from typing import Any

import pytest
from pytest_django import Settings

from agents.models import SystemNote
from agents.producer import CLEAN_PICTURE_USD
from chat.models import Message
from gateway.fake import FakeModel, turn
from gateway.models import ModelCall
from gateway.types import BlockedBySafetyFilter, ModelRequest, UnusableReply
from jobs import fix_check, reference_photos, work
from jobs.clip_judge import (
    ASKS,
    MOTION_CHECK,
    MOTION_FIX_QUESTION,
    MOTION_PHOTO_FIX_QUESTION,
    OWNER_PHOTO_LABEL,
    wording_version,
)
from jobs.fix_check import PhotoFixVerdict
from jobs.models import (
    Fix,
    FixPlan,
    FixPlanScene,
    Job,
    ProducedItem,
    ReferencePhoto,
    Scene,
    SceneStep,
)
from jobs.scenes import SAME_SIDE

from . import fixing
from .agent_tries import agent_films
from .conftest import (
    CLIP_FIXED,
    CLIP_MOTION_OK,
    FACTS_OK,
    FIX_MADE,
    LABELLED,
    NO_CHOICES,
    OWNER_PHOTO_SHOWS,
    PICTURE_OK,
    PLAN,
    HeldSteps,
    broll,
    handoffs,
    motion_answer,
    paid_for,
    picture,
    with_owner_photo,
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
from .test_producer_broll_picture_check import FUSED_HANDLE, impossible

pytestmark = pytest.mark.django_db(transaction=True)

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


def photo(number: int, thing: str, **answers: bool) -> dict[str, Any]:
    """Owner photo `number` on a plan row, taken as `thing`, routed as the decider answers."""
    return fixing.owner_photo(number, thing, owner_file(number), **answers)


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


def errors() -> list[str]:
    """What the producer was told went wrong, oldest first."""
    return list(
        SystemNote.objects.filter(kind=SystemNote.Kind.ERROR)
        .order_by("created_at", "id")
        .values_list("text", flat=True)
    )


def problems() -> list[str]:
    """What the chat told the shop owner went wrong."""
    return list(Message.objects.filter(level="problem").values_list("text", flat=True))


def brighter(fake_model: FakeModel, steps: HeldSteps) -> None:
    """A later fix of scene 2's starting picture, with no photo, said yes to and made."""
    fake_model.respond("choose_broll_picture", BROLL_CHOICE)
    fixing.fixed(2, "new_picture", note="Make it brighter.", change=BRIGHTER)
    steps.run_held()


# --- Decision 5's fallback: the route call in place of the decider's answers -------------------


@pytest.fixture
def routes(fake_model: FakeModel, settings: Settings) -> dict[tuple[str, str], str]:
    """The route call switched on after the decider (decision 5's fallback), saying where
    each (thing, scene shows) goes: in the starting picture unless a test says otherwise."""
    settings.ROUTE_OWNER_PHOTOS_AFTER_THE_DECIDER = True
    answers: dict[tuple[str, str], str] = {}

    def answering(request: ModelRequest[Any]) -> dict[str, Any]:
        handoff = request.handoff
        assert isinstance(handoff, reference_photos.RouteHandoff)
        return where(answers.get((handoff.thing, handoff.scene_shows), "in_the_picture"))

    fake_model.answer_unscripted("route_reference_photo", answering)
    return answers


def test_with_the_fallback_a_scene_whose_new_line_kept_its_picture_is_still_routed(
    fake_model: FakeModel,
    shown: ProducedItem,
    say: Say,
    steps: HeldSteps,
    routes: dict[tuple[str, str], str],
) -> None:
    # The scene's line changed and its picture was kept: no picture is made for the new line.
    fixing.fixed(2, "new_line", note="Change scene 2's line.", change="A new line.", line=LINE)
    steps.run_held()
    photos_sent(fake_model, say, OWNER_MUG)

    plan = fixing.planned(
        fixing.plan_row(
            2,
            "new_clip",
            change=MUG_FIX,
            photos=[photo(1, "black mug", in_picture=False, during_action=True)],
        ),
        note=MUG_NOTE,
    )

    # The route call still looks at the picture the shop owner sees, and puts the mug in it.
    assert len(handoffs("route_reference_photo")) == 1
    assert plan.scenes.get().action == "new_picture"


# The route call's "in the picture" keeps the decider's "more during the action"; "from the
# start" is in the picture only.
@pytest.mark.parametrize(
    ("answer", "during_action"), [("in_the_picture", True), ("belongs_from_the_start", False)]
)
def test_with_the_fallback_a_thing_the_route_call_puts_in_the_picture_is_a_new_picture(
    fake_model: FakeModel,
    shown: ProducedItem,
    say: Say,
    routes: dict[tuple[str, str], str],
    answer: str,
    during_action: bool,
) -> None:
    # D6: a thing to add from the start, sent to the clip, would pop in mid-clip.
    routes[("black mug", SHOWS)] = answer
    picture_2 = latest("starting_picture")
    photos_sent(fake_model, say, OWNER_MUG)

    # The decider said a new clip, the mug only during the action; the route call finds it
    # belongs in the picture.
    plan = fixing.planned(
        fixing.plan_row(
            2,
            "new_clip",
            change=MUG_FIX,
            photos=[photo(1, "black mug", in_picture=False, during_action=True)],
        ),
        note=MUG_NOTE,
    )

    row = plan.scenes.get()
    assert row.action == "new_picture"
    assert (row.photos[0]["in_picture"], row.photos[0]["during_action"]) == (True, during_action)
    asked = handoffs("route_reference_photo")[0]
    assert asked == {
        "thing": "black mug",
        "scene_shows": SHOWS,
        "change": MUG_FIX,
        "pictures": "The starting picture is first; the shop owner's photo is second.",
    }
    call = ModelCall.objects.filter(purpose="route_reference_photo").first()
    assert call is not None and call.images == [
        {"label": "The starting picture:", "key": picture_2.file},
        {"label": "The shop owner's photo:", "key": owner_file(1)},
    ]
    # The clean picture is counted once, in the estimate the shop owner is shown.
    assert row.estimate_usd > CLEAN_PICTURE_USD
    # Nothing is drawn or kept before their yes.
    assert clean_pictures() == []
    assert not ReferencePhoto.objects.exists()


def test_with_the_fallback_and_no_answer_from_the_route_call_the_deciders_answers_stand(
    fake_model: FakeModel, shown: ProducedItem, say: Say, routes: dict[tuple[str, str], str]
) -> None:
    fake_model.respond(
        "route_reference_photo", *[UnusableReply("unreadable", input_tokens=1, output_tokens=1)] * 2
    )
    photos_sent(fake_model, say, OWNER_TEA)

    plan = fixing.planned(
        fixing.plan_row(
            2,
            "new_clip",
            change=TEA_FIX,
            photos=[photo(1, "tea", in_picture=False, during_action=True)],
        ),
        note=TEA_NOTE,
    )

    row = plan.scenes.get()
    assert (row.action, row.cant) == ("new_clip", "")
    assert (row.photos[0]["in_picture"], row.photos[0]["during_action"]) == (False, True)


def test_with_the_fallback_a_photo_the_thing_cant_be_seen_in_cant_be_used_and_makes_nothing(
    fake_model: FakeModel, shown: ProducedItem, say: Say, routes: dict[tuple[str, str], str]
) -> None:
    # D8, from the owner photos test's C16: a blurry photo was drawn as a guess, so the
    # route call says first whether the thing can be copied from it (design thread 22:15).
    routes[("black mug", SHOWS)] = "unclear"
    photos_sent(fake_model, say, OWNER_MUG)

    plan = fixing.planned(
        fixing.plan_row(2, "new_picture", change=MUG_FIX, photos=[photo(1, "black mug")]),
        note=MUG_NOTE,
    )

    assert plan.scenes.get().cant == (
        "Owner photo 1 doesn't show the black mug clearly enough to copy its look. Send a "
        "clearer photo of the black mug."
    )
    fixing.say_yes(plan)
    assert not Fix.objects.exists()
    assert handoffs("make_reference_photo_picture") == []


def test_the_route_call_looks_at_the_photo_and_is_lenient_about_it() -> None:
    fields = list(reference_photos.RouteAnswer.model_fields)
    assert fields[:3] == ["photo_shows", "clear_enough", "unclear_why"]
    instructions = " ".join(reference_photos.ROUTE_INSTRUCTIONS.split())
    assert "An ordinary phone photo is clear enough" in instructions
    assert "answer no only when you can't really see the thing" in instructions


def test_the_route_call_is_answered_from_its_record_when_planned_again(
    fake_model: FakeModel, shown: ProducedItem, say: Say, routes: Any
) -> None:
    photos_sent(fake_model, say, OWNER_MUG)
    for _ in range(2):
        fixing.planned(
            fixing.plan_row(2, "new_picture", change=MUG_FIX, photos=[photo(1, "black mug")]),
            note=MUG_NOTE,
        )

    assert ModelCall.objects.filter(purpose="route_reference_photo").count() == 1


def test_a_route_answer_kept_in_an_older_form_counts_as_no_answer(
    fake_model: FakeModel, shown: ProducedItem, say: Say, routes: Any
) -> None:
    routes[("tea", SHOWS)] = "appears_during_the_clip"
    photos_sent(fake_model, say, OWNER_TEA)
    fixing.planned(tea_plan_row(in_picture=True), note=TEA_NOTE)
    assert ModelCall.objects.filter(purpose="route_reference_photo").count() == 1
    # As kept before the answer had "clear_enough" (owner photos test, 23:53).
    ModelCall.objects.filter(purpose="route_reference_photo").update(
        output={"where": "in_the_picture"}
    )

    plan = fixing.planned(tea_plan_row(in_picture=True), note=TEA_NOTE)

    # No answer could be had, so the decider's own answers stand: the tea in the picture.
    row = plan.scenes.get()
    assert (row.action, row.cant, row.photos[0]["in_picture"]) == ("new_picture", "", True)


# --- The photo numbers of the chat ------------------------------------------------------------


def test_a_note_per_photo_planned_together_takes_each_photo_by_its_number(
    fake_model: FakeModel, shown: ProducedItem, say: Say
) -> None:
    fake_model.respond("produce", turn(says="And the other photo?"))
    say("This is our mug.", OWNER_MUG)
    fake_model.respond(
        "decide_fixes",
        turn(
            calls=[
                ("sort_message", {"kind": "fix_note"}),
                ("look_at_owner_photo", {"photo": 1}),
                ("look_at_owner_photo", {"photo": 2}),
                ("look_at_references", {"scene": 2}),
                (
                    "decide",
                    {
                        "scenes": [
                            {
                                "scene": 2,
                                "action": "fix",
                                "change": "In scene 2, the black mug and their own tea.",
                                "line": "",
                                "go_back_to": None,
                                "cant": "",
                                "photos": [
                                    {"photo": 1, "in_picture": True, "during_action": False},
                                    {"photo": 2, "in_picture": True, "during_action": False},
                                ],
                            }
                        ],
                        "photos": [
                            {"photo": 1, "thing": "black mug", "clear": True},
                            {"photo": 2, "thing": "tea", "clear": True},
                        ],
                        "cants": [],
                        "question": "",
                    },
                ),
            ]
        ),
    )

    say("And this is our tea.", OWNER_TEA)

    plan = FixPlan.objects.get()
    files = [each["file"] for each in plan.scenes.get().photos]
    assert files == [owner_file(1), owner_file(2)]


# --- A new starting picture with the photo -----------------------------------------------------


@pytest.fixture
def mug_fixed(fake_model: FakeModel, shown: ProducedItem, steps: HeldSteps, say: Say) -> Fix:
    """Scene 2 drawn again with the shop owner's black mug, and its clip made from it by
    code."""
    photos_sent(fake_model, say, OWNER_MUG)
    fake_model.respond("choose_broll_picture", BROLL_CHOICE)
    fixing.fixed(2, "new_picture", note=MUG_NOTE, change=MUG_FIX, photos=[photo(1, "black mug")])
    steps.run_held()
    return Fix.objects.get()


def test_the_new_starting_picture_is_drawn_with_the_clean_picture_first_labelled_to_win(
    fake_model: FakeModel, mug_fixed: Fix
) -> None:
    (clean,) = clean_pictures()
    # The clean picture is drawn from the shop owner's photo alone, and never copies a face.
    drawing = handoffs("make_reference_photo_picture")[0]
    assert drawing == {
        "prompt": reference_photos.clean_picture_prompt("black mug"),
        "pictures": [owner_file(1)],
    }
    assert "Never copy any person's face." in drawing["prompt"]
    # Its check is the starting picture's, beside the shop owner's photo.
    checked = ModelCall.objects.filter(purpose="check_starting_picture").order_by("id")
    assert any(
        call.images[0]["key"] == clean and call.images[1]["key"] == owner_file(1)
        for call in checked
    )
    step = last_step("starting_picture")
    planned = handoffs("choose_broll_picture")[-1]
    assert planned["pictures"][0] == {
        "image": 1,
        "job": "the shop owner's own black mug: wherever it disagrees with another picture, "
        "follow this one",
    }
    assert planned["pictures"][1]["image"] == 2
    asked = ModelCall.objects.filter(purpose="choose_broll_picture").last()
    assert asked is not None and asked.images[0] == {
        "label": "The shop owner's own black mug",
        "key": clean,
    }
    drawn = handoffs("make_starting_picture")[-1]
    assert drawn["prompt"].startswith(
        "Image 1 is the shop owner's own black mug: wherever it disagrees with another "
        "picture, follow Image 1. "
    )
    assert drawn["pictures"][0] == clean
    assert step.pictures_sent[0]["reference_photo"] == ReferencePhoto.objects.get().pk
    # The clip of the new picture is made from it alone, as any clip: the mug is in its
    # picture, so no clean picture goes with it.
    sent = handoffs("make_broll_clip")[-1]
    assert sent["starting_picture"] is None
    assert sent["example_pictures"] == [latest("starting_picture").file]
    assert sent["prompt"].startswith(
        "Image 1 is how the clip starts: the clip starts exactly like Image 1. A "
    )


OWNER_COASTER = ("my-coaster.png", picture(60, 80, (90, 60, 30)))


def test_with_three_of_the_shop_owners_things_the_product_photo_is_still_photo_1(
    fake_model: FakeModel,
    shown: ProducedItem,
    steps: HeldSteps,
    say: Say,
    instructed: dict[str, list[str]],  # noqa: F811 (the fixture)
) -> None:
    # Case C12b, replayed free: three things drawn in first made the product photo Image 4,
    # and the writer, told so, answered "photo" with 4. With one product photo it is no
    # longer asked which.
    Job.objects.get().photos.filter(position=2).delete()
    photos_sent(fake_model, say, OWNER_MUG, OWNER_TEA, OWNER_COASTER)
    asked: list[dict[str, Any]] = []

    def answer(request: ModelRequest[Any]) -> dict[str, Any]:
        asked.append(request.output.model_json_schema())
        return {key: value for key, value in BROLL_CHOICE.items() if key != "photo"}

    fake_model.answer_unscripted("choose_broll_picture", answer)
    fixing.fixed(
        2,
        "new_picture",
        change=MUG_FIX,
        photos=[photo(1, "black mug"), photo(2, "tea"), photo(3, "coaster")],
    )
    steps.run_held()

    assert "photo" not in asked[0]["properties"]
    assert "Image 4" not in instructed["choose_broll_picture"][-1]
    step = last_step("starting_picture")
    assert step.status == "finished"
    assert step.photo is not None and step.photo.position == 1
    drawn = handoffs("make_starting_picture")[-1]
    assert drawn["pictures"][3] == Job.objects.get().photos.get(position=1).file
    assert "Image 4 is the product photo. " in drawn["prompt"]


def test_the_writer_of_the_prompts_is_told_the_shop_owners_pictures_win(
    instructed: dict[str, list[str]],  # noqa: F811 (the fixture)
    mug_fixed: Fix,
) -> None:
    assert instructed["choose_broll_picture"][-1].endswith(
        reference_photos.writer_rules(["black mug"], [], picks_photo=True)
    )


def test_the_fix_check_is_shown_the_shop_owners_photo_and_looks_at_it_first(
    mug_fixed: Fix,
) -> None:
    asked = ModelCall.objects.filter(purpose="check_fix").order_by("id")
    first = asked.first()
    assert first is not None
    assert first.handoff["owner_photos"] == ["black mug"]
    assert first.images[2] == {
        "label": "The shop owner's photo of their black mug:",
        "key": owner_file(1),
    }
    check = mug_fixed.checks.get()
    assert check.verdict == "passed"
    assert check.answers[0]["owner_photo_shows"] == OWNER_PHOTO_SHOWS
    # It writes what the photo shows before anything else.
    assert list(PhotoFixVerdict.model_fields)[0] == "owner_photo_shows"


def test_a_shop_owners_photo_the_fix_checks_filter_refuses_is_left_out_and_the_fix_checked(
    fake_model: FakeModel, shown: ProducedItem, steps: HeldSteps, say: Say
) -> None:
    """Requirement 22: the fix check is asked again without the photo, as a fix with no photo
    is, and answers on the old and new pictures; the chat is told in red."""
    label = "The shop owner's photo of their black mug:"

    def refusing(request: ModelRequest[Any]) -> dict[str, Any]:
        if label in [image.label for image in request.images]:
            raise BlockedBySafetyFilter("content_policy_violation")
        return with_owner_photo(FIX_MADE, request.output)

    fake_model.answer_unscripted("check_fix", refusing)
    photos_sent(fake_model, say, OWNER_MUG)
    fake_model.respond("choose_broll_picture", BROLL_CHOICE)
    fixing.fixed(2, "new_picture", note=MUG_NOTE, change=MUG_FIX, photos=[photo(1, "black mug")])
    steps.run_held()

    asked = ModelCall.objects.filter(purpose="check_fix").order_by("id")
    with_it = ["The old picture:", "The new picture:", label]
    assert [[image["label"] for image in call.images] for call in asked] == [
        with_it,
        with_it[:2],
    ] * 2
    assert all("owner_photos" not in call.handoff for call in asked[1::2])
    check = Fix.objects.get().checks.get()
    assert check.verdict == "passed"
    assert [answer["not_shown"] for answer in check.answers] == [
        ["the shop owner's photo of their black mug"]
    ] * 2
    (said,) = [text for text in problems() if "safety filter" in text]
    assert "the shop owner's photo of their black mug" in said and "scene 2's fix" in said
    assert said in [warning["text"] for warning in Job.objects.get().warnings]


def test_a_shop_owners_photo_the_clean_pictures_check_refuses_is_named_as_theirs(
    fake_model: FakeModel, shown: ProducedItem, steps: HeldSteps, say: Say
) -> None:
    """Requirement 22: the clean picture is checked without the shop owner's photo it was
    drawn from, and the red notice names that photo as theirs, not a shop photo."""

    def refusing(request: ModelRequest[Any]) -> dict[str, Any]:
        if owner_file(1) in [image.key for image in request.images]:
            raise BlockedBySafetyFilter("content_policy_violation")
        return PICTURE_OK

    fake_model.answer_unscripted("check_starting_picture", refusing)
    photos_sent(fake_model, say, OWNER_MUG)
    fake_model.respond("choose_broll_picture", BROLL_CHOICE)
    fixing.fixed(2, "new_picture", note=MUG_NOTE, change=MUG_FIX, photos=[photo(1, "black mug")])
    steps.run_held()

    assert last_step("starting_picture").status == "finished"
    (said,) = [text for text in problems() if "safety filter" in text]
    assert said == (
        "The AI model's safety filter refused to look at the shop owner's photo of their "
        "black mug, so the clean picture of the shop owner's black mug was checked without it."
    )


def test_the_photo_is_kept_on_the_scene_once_the_shop_owner_says_yes(mug_fixed: Fix) -> None:
    row = ReferencePhoto.objects.get()
    assert (row.scene.number, row.thing, row.use, row.added_by) == (
        2,
        "black mug",
        "picture",
        mug_fixed,
    )
    assert row.drawn == clean_pictures()[0]
    assert list(last_step("starting_picture").reference_photos.all()) == [row]
    assert list(last_step("clip").reference_photos.all()) == [row]


# --- Reference mode ------------------------------------------------------------------------------


REWRITE: dict[str, Any] = {
    "motion_prompt": "Tea pours from the teapot into the mug.",
    "motion_prompt_reason": "The note is about the tea.",
}


def tea_plan_row(*, in_picture: bool = False) -> FixPlanScene:
    """Scene 2's new clip with owner photo 1's tea, which shows more during the action."""
    return fixing.plan_row(
        2,
        "new_clip",
        change=TEA_FIX,
        photos=[photo(1, "tea", in_picture=in_picture, during_action=True)],
    )


@pytest.fixture
def tea_fixed(fake_model: FakeModel, shown: ProducedItem, steps: HeldSteps, say: Say) -> Fix:
    """Scene 2's clip made again in reference mode with the shop owner's tea."""
    photos_sent(fake_model, say, OWNER_TEA)
    fake_model.respond("rewrite_clip_prompt", REWRITE)
    fixing.say_yes(fixing.planned(tea_plan_row(), note=TEA_NOTE))
    steps.run_held()
    return Fix.objects.get()


def test_a_thing_during_the_clip_makes_it_in_reference_mode_from_the_starting_picture(
    tea_fixed: Fix,
) -> None:
    (clean,) = clean_pictures()
    sent = handoffs("make_broll_clip")[-1]
    # Locked rule 1 as changed (Julian 19:38): the drawn starting picture goes first, then the
    # clean picture; never a raw photo, and never both ways at once.
    assert sent["starting_picture"] is None
    assert sent["example_pictures"] == [latest("starting_picture").file, clean]
    assert owner_file(1) not in sent["example_pictures"]
    assert sent["prompt"].startswith(
        "Image 1 is how the clip starts: the clip starts exactly like Image 1. Image 2 is the "
        "shop owner's own tea: wherever it disagrees with another picture, follow Image 2. A "
    )
    assert f"The camera stays still. {SAME_SIDE} {REWRITE['motion_prompt']}" in sent["prompt"]
    # The starting picture isn't drawn again.
    assert latest("starting_picture").version == 1
    assert in_use() == [("tea", "clip")]


@pytest.fixture
def reference_mode_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """The one-line switch back to before (Julian 2026-10-16 19:08)."""
    monkeypatch.setattr(work, "EVERY_BROLL_CLIP_IN_REFERENCE_MODE", False)


def test_with_reference_mode_switched_off_only_a_thing_during_the_clip_makes_it_so(
    reference_mode_off: None, tea_fixed: Fix
) -> None:
    (clean,) = clean_pictures()
    first, remade = handoffs("make_broll_clip")[0], handoffs("make_broll_clip")[-1]
    # Before the tea, the clip was made from its starting picture as its first frame.
    assert (first["starting_picture"], first["example_pictures"]) == (
        latest("starting_picture").file,
        [],
    )
    assert not first["prompt"].startswith("Image 1")
    # With the tea, in reference mode as the owner-photos build made it.
    assert remade["starting_picture"] is None
    assert remade["example_pictures"] == [latest("starting_picture").file, clean]
    assert remade["prompt"].startswith(
        "Image 1 is how the clip starts: the clip starts exactly like Image 1. Image 2 is the "
        "shop owner's own tea"
    )


def test_the_clip_prompt_writer_is_told_of_the_shop_owners_thing_and_shown_it(
    tea_fixed: Fix,
) -> None:
    call = ModelCall.objects.filter(purpose="rewrite_clip_prompt").last()
    assert call is not None
    assert call.images[1] == {"label": "The shop owner's own tea", "key": clean_pictures()[0]}


def test_the_clip_judge_is_shown_the_shop_owners_photo_for_the_change(
    instructed: dict[str, list[str]],  # noqa: F811 (the fixture)
    tea_fixed: Fix,
) -> None:
    motion = ModelCall.objects.filter(purpose="check_clip_motion").order_by("-id")[:ASKS]
    for call in motion:
        assert call.handoff["owner_photos"] == ["tea"]
        assert {"label": OWNER_PHOTO_LABEL.format("tea"), "key": owner_file(1)} in call.images
    # SIDES is judged as any clip, without the photo.
    sides = ModelCall.objects.filter(purpose="check_clip_sides").order_by("-id")[:ASKS]
    assert all("owner_photos" not in call.handoff for call in sides)
    check = tea_fixed.checks.get()
    assert check.verdict == "passed"
    assert all(
        told.endswith(MOTION_PHOTO_FIX_QUESTION) for told in instructed["check_clip_motion"][-ASKS:]
    )


def test_a_shop_owners_photo_the_clip_judges_filter_refuses_is_left_out_of_the_clip_fix_check(
    fake_model: FakeModel,
    shown: ProducedItem,
    steps: HeldSteps,
    say: Say,
    instructed: dict[str, list[str]],  # noqa: F811 (the fixture)
) -> None:
    """Requirement 22: MOTION is asked again without the photo, as a fix without photos is,
    and its answer about the change still settles the fix; the chat is told in red."""
    label = OWNER_PHOTO_LABEL.format("tea")
    answering = motion_answer(CLIP_MOTION_OK)

    def refusing(request: ModelRequest[Any]) -> dict[str, Any]:
        if label in [image.label for image in request.images]:
            raise BlockedBySafetyFilter("content_policy_violation")
        return answering(request)

    fake_model.answer_unscripted("check_clip_motion", refusing)
    photos_sent(fake_model, say, OWNER_TEA)
    fake_model.respond("rewrite_clip_prompt", REWRITE)
    fixing.say_yes(fixing.planned(tea_plan_row(), note=TEA_NOTE))
    steps.run_held()

    *_, first, then = ModelCall.objects.filter(purpose="check_clip_motion").order_by("id")
    assert label in [image["label"] for image in first.images]
    assert [image for image in then.images if image["label"] != label] == then.images
    assert len(then.images) == len(first.images) - 1
    assert "owner_photos" not in then.handoff
    assert instructed["check_clip_motion"][-2].endswith(MOTION_PHOTO_FIX_QUESTION)
    assert instructed["check_clip_motion"][-1] == f"{MOTION_CHECK}\n\n{MOTION_FIX_QUESTION}"
    new = latest("clip")
    assert new.ruling is not None
    assert new.ruling["wording"] == wording_version(fix=True)
    assert "owner_photo_shows" not in new.ruling["answers"]["motion"][0]["owner_change"]
    fix = Fix.objects.get()
    assert fix.checks.get().verdict == "passed"
    (said,) = [text for text in problems() if "safety filter" in text]
    assert "the shop owner's photo of their tea" in said and "scene 2's clip" in said


def test_a_try_after_the_judge_fails_stays_in_reference_mode(
    fake_model: FakeModel,
    shown: ProducedItem,
    steps: HeldSteps,
    say: Say,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    photos_sent(fake_model, say, OWNER_TEA)
    failing = {
        **CLIP_MOTION_OK,
        "real_motion": {"verdict": "fail", "problem": "The tea floats.", "back_to_normal_at": None},
    }
    fixed = {"owner_photo_shows": OWNER_PHOTO_SHOWS, **CLIP_FIXED}
    fake_model.respond("check_clip_motion", {**failing, "owner_change": fixed})
    fake_model.respond("rewrite_clip_prompt", REWRITE)
    agent_films(monkeypatch, REWRITE["motion_prompt"])

    fixing.say_yes(fixing.planned(tea_plan_row(), note=TEA_NOTE))
    steps.run_held()

    # Scenes 2 and 3's first clips, then scene 2's fix and the agent's try once the judge
    # failed it.
    assert len(handoffs("make_broll_clip")) == 2 + 2
    remade = handoffs("make_broll_clip")[-1]
    assert remade["starting_picture"] is None
    assert len(remade["example_pictures"]) == 2
    assert remade["prompt"].startswith("Image 1 is how the clip starts")


# --- Several photos on one note ------------------------------------------------------------------


MUG_AND_TEA = "In scene 2, the mug and the tea look like the shop owner's."


def mug_and_tea(fake_model: FakeModel, steps: HeldSteps, say: Say) -> None:
    """Scene 2's picture drawn again with the black mug, the tea only during its clip, and
    its clip made from it by code."""
    photos_sent(fake_model, say, OWNER_MUG, OWNER_TEA)
    fake_model.respond("choose_broll_picture", BROLL_CHOICE)
    fake_model.respond("rewrite_clip_prompt", REWRITE)
    fixing.fixed(
        2,
        "new_picture",
        note="Our mug and our tea.",
        change=MUG_AND_TEA,
        photos=[photo(1, "black mug"), photo(2, "tea", in_picture=False, during_action=True)],
    )
    steps.run_held()


def test_one_note_with_two_photos_takes_one_thing_from_each(
    fake_model: FakeModel, shown: ProducedItem, steps: HeldSteps, say: Say
) -> None:
    mug_and_tea(fake_model, steps, say)

    mug, tea = clean_pictures()
    # The mug is drawn into the picture; the tea only goes to the clip.
    assert handoffs("make_starting_picture")[-1]["pictures"][0] == mug
    assert tea not in handoffs("make_starting_picture")[-1]["pictures"]
    sent = handoffs("make_broll_clip")[-1]
    assert sent["example_pictures"] == [latest("starting_picture").file, tea]
    assert sorted(in_use()) == [("black mug", "picture"), ("tea", "clip")]


def test_a_picture_fix_check_looks_only_at_things_drawn_into_the_picture(
    fake_model: FakeModel, shown: ProducedItem, steps: HeldSteps, say: Say
) -> None:
    # The tea only shows during the clip: it is never in a still, so the picture's check
    # isn't told to look for it.
    mug_and_tea(fake_model, steps, say)

    asked = ModelCall.objects.filter(purpose="check_fix").order_by("id")
    assert asked.exists()
    for call in asked:
        assert call.handoff["owner_photos"] == ["black mug"]
        # It is told the tea isn't expected in a still (design thread 23:54).
        assert call.handoff["during_the_action"] == ["tea"]
        assert [image["key"] for image in call.images[2:]] == [owner_file(1)]


def test_a_thing_during_the_clip_in_a_picture_fix_is_checked_on_its_clip(
    fake_model: FakeModel, shown: ProducedItem, steps: HeldSteps, say: Say
) -> None:
    # The tea only shows during the clip, so the picture's check can't see it: the clip made
    # from the new picture is asked about it, with its photo (Julian 20:25, "we always check
    # whether the owner's words are applied right"; design thread 22:42).
    old_clip = latest("clip")

    mug_and_tea(fake_model, steps, say)

    motion = ModelCall.objects.filter(purpose="check_clip_motion").order_by("-id")[:ASKS]
    for call in motion:
        assert call.handoff["owner_photos"] == ["tea"]
        assert {"label": OWNER_PHOTO_LABEL.format("tea"), "key": owner_file(2)} in call.images
        assert {"label": OWNER_PHOTO_LABEL.format("black mug"), "key": owner_file(1)} not in (
            call.images
        )
    picture_check, clip_check = Fix.objects.get(scene__number=2).checks.order_by("id")
    assert (picture_check.step.kind, clip_check.step.kind) == ("starting_picture", "clip")
    assert (clip_check.old, clip_check.verdict) == (old_clip, "passed")
    assert latest("clip").ruling["compared_with"] == old_clip.pk  # type: ignore[index]


def test_a_picture_fix_with_no_thing_during_the_clip_leaves_its_clip_judged_as_any(
    mug_fixed: Fix,
) -> None:
    motion = ModelCall.objects.filter(purpose="check_clip_motion").order_by("-id")[:ASKS]
    assert all("owner_change" not in call.handoff for call in motion)
    assert all("owner_photos" not in call.handoff for call in motion)
    assert [check.step.kind for check in mug_fixed.checks.all()] == ["starting_picture"]
    assert "compared_with" not in (latest("clip").ruling or {})


def test_a_note_changing_the_picture_in_words_beside_a_clip_photo_redraws_the_picture(
    fake_model: FakeModel,
    shown: ProducedItem,
    steps: HeldSteps,
    say: Say,
    instructed: dict[str, list[str]],  # noqa: F811 (the fixture)
) -> None:
    # "A dark walnut bench, and our tea": the tea only shows during the clip, but the bench is
    # a change to the picture, so it is drawn again (review follow-up, design thread 23:54).
    photos_sent(fake_model, say, OWNER_TEA)
    fix = "In scene 2, the bench is dark walnut and the tea looks like the shop owner's."
    fake_model.respond("choose_broll_picture", BROLL_CHOICE)
    fake_model.respond("rewrite_clip_prompt", REWRITE)

    fixing.fixed(
        2,
        "new_picture",
        note="Put it on a dark walnut bench, and use our tea.",
        change=fix,
        photos=[photo(1, "tea", in_picture=False, during_action=True)],
    )
    asked = Fix.objects.get()
    assert (asked.redo, asked.photos[0]["use"]) == ("picture", "clip")
    steps.run_held()

    checked = ModelCall.objects.filter(purpose="check_fix").order_by("id")
    assert checked.exists()
    for call in checked:
        assert call.handoff["during_the_action"] == ["tea"]
        assert "owner_photos" not in call.handoff
        assert len(call.images) == 2
    assert all(told.endswith(fix_check.DURING_THE_ACTION) for told in instructed["check_fix"])
    # Its clip's judge looks for the tea, with its photo.
    motion = ModelCall.objects.filter(purpose="check_clip_motion").order_by("-id")[:ASKS]
    assert all(call.handoff["owner_photos"] == ["tea"] for call in motion)


def test_a_picture_fix_with_no_photo_is_checked_as_before(
    fake_model: FakeModel,
    shown: ProducedItem,
    steps: HeldSteps,
    instructed: dict[str, list[str]],  # noqa: F811 (the fixture)
) -> None:
    brighter(fake_model, steps)

    asked = ModelCall.objects.filter(purpose="check_fix")
    assert asked.exists()
    for call in asked:
        assert set(call.handoff) == {"fix", "ask", "pictures"}
    assert all(told == fix_check.FIX_CHECK for told in instructed["check_fix"])


# --- The photo stays with the scene (D1) --------------------------------------------------------


def test_a_later_fix_on_the_scene_draws_it_with_the_photo_again_without_drawing_it_again(
    fake_model: FakeModel, mug_fixed: Fix, steps: HeldSteps
) -> None:
    brighter(fake_model, steps)

    assert len(clean_pictures()) == 1
    assert handoffs("make_starting_picture")[-1]["pictures"][0] == clean_pictures()[0]
    assert in_use() == [("black mug", "picture")]
    # The check of that fix has no photo of its own: it is asked as any.
    assert "owner_photos" not in handoffs("check_fix")[-1]


def test_a_new_line_keeps_the_picture_with_the_photo(
    fake_model: FakeModel, mug_fixed: Fix, steps: HeldSteps
) -> None:
    line = LINE
    with_the_mug = latest("starting_picture")
    drawn = len(handoffs("make_starting_picture"))

    fixing.fixed(2, "new_line", note="Change scene 2's line.", change="A new line.", line=line)
    steps.run_held()

    assert Scene.objects.get(number=2).line == line
    # Julian, 2026-10-17: a new line keeps the starting picture, the owner's mug in it.
    assert len(handoffs("make_starting_picture")) == drawn
    assert latest("starting_picture") == with_the_mug
    assert latest("clip").picture == with_the_mug
    assert latest("clip").step.line == line  # type: ignore[union-attr]


# --- Going back, a new photo of the same thing ---------------------------------------------------


def test_going_back_from_a_clip_in_reference_mode_brings_back_the_clip_from_before(
    tea_fixed: Fix, steps: HeldSteps
) -> None:
    old_clip = ProducedItem.objects.get(kind="clip", scene__number=2, version=1)
    paid = paid_for()

    plan = fixing.fixed(2, "go_back", note="Go back to the old tea.", go_back_to=1)
    steps.run_held()

    assert plan.scenes.get().estimate_usd == Decimal(0)
    assert paid_for() == paid
    assert latest("clip").file == old_clip.file
    assert in_use() == []


def test_a_new_photo_of_the_same_thing_replaces_the_old_one(
    fake_model: FakeModel, mug_fixed: Fix, steps: HeldSteps, say: Say
) -> None:
    photos_sent(fake_model, say, ("other-mug.png", picture(60, 80, (5, 5, 40))))
    fix = "In scene 2, the mug looks like the shop owner's new photo of their black mug."
    fake_model.respond("choose_broll_picture", BROLL_CHOICE)

    fixing.fixed(2, "new_picture", change=fix, photos=[photo(2, "the Black mug")])
    steps.run_held()

    rows = ReferencePhoto.objects.filter(scene__number=2).order_by("id")
    old, new = rows
    assert old.dropped_by == new.added_by
    assert (new.file, new.dropped_by) == (owner_file(2), None)
    assert handoffs("make_starting_picture")[-1]["pictures"][0] == clean_pictures()[-1]


def test_a_new_photo_of_a_thing_another_scene_uses_is_taken_only_by_the_scene_planned(
    fake_model: FakeModel, tea_fixed: Fix, steps: HeldSteps, say: Say
) -> None:
    # Scene 2 keeps its tea when a new photo of it is planned for scene 3 alone.
    photos_sent(fake_model, say, ("other-tea.png", picture(60, 80, (200, 120, 40))))
    fake_model.respond("rewrite_clip_prompt", REWRITE)

    fixing.fixed(
        3,
        "new_clip",
        change="In scene 3, the tea looks like the shop owner's new photo.",
        photos=[photo(2, "tea", in_picture=False, during_action=True)],
    )
    steps.run_held()

    assert in_use(3) == [("tea", "clip")]
    assert in_use(2) == [("tea", "clip")]
    assert ReferencePhoto.objects.get(scene__number=2, dropped_by__isnull=True).file == (
        owner_file(1)
    )


def test_the_same_photo_named_in_other_capitals_is_already_in_use_and_makes_nothing(
    fake_model: FakeModel, mug_fixed: Fix, steps: HeldSteps
) -> None:
    made = latest("starting_picture")
    paid = paid_for()

    plan = fixing.fixed(
        2, "new_picture", note="Again, please.", change=MUG_FIX, photos=[photo(1, "Black Mug")]
    )
    steps.run_held()

    assert plan.scenes.get().cant == (
        "Scene 2 already uses owner photo 1 for the Black Mug, so nothing would change."
    )
    assert paid_for() == paid
    assert latest("starting_picture") == made


# --- A photo that can't be drawn clean (D8) -----------------------------------------------------


def test_a_clean_picture_that_keeps_failing_its_check_asks_for_another_photo(
    fake_model: FakeModel, shown: ProducedItem, steps: HeldSteps, say: Say
) -> None:
    photos_sent(fake_model, say, OWNER_MUG)
    fake_model.respond("check_starting_picture", *[impossible(FUSED_HANDLE)] * 6)
    old = latest("clip")

    fixing.fixed(2, "new_picture", change=MUG_FIX, photos=[photo(1, "black mug")])
    steps.run_held()

    step = last_step("starting_picture")
    assert step.status == "failed"
    assert len(clean_pictures()) == 3
    assert not handoffs("choose_broll_picture")[3:]
    assert "Ask them for another photo of the black mug" in step.result
    assert any("Ask them for another photo of the black mug" in told for told in errors())
    assert latest("clip").file == old.file
    assert (
        "Scene 2's starting picture couldn't be made: your black mug couldn't be drawn from "
        "your photo. Please send another photo of it."
    ) in problems()
    assert Scene.objects.get(number=2).status == "finished"


def test_a_photo_the_picture_model_refuses_asks_for_another_photo(
    fake_model: FakeModel, shown: ProducedItem, steps: HeldSteps, say: Say
) -> None:
    photos_sent(fake_model, say, OWNER_MUG)
    original = fake_model.edit

    def refusing(**asked: Any) -> Any:
        if asked["prompt"].startswith("A clean photo of only"):
            raise BlockedBySafetyFilter("refused")
        return original(**asked)

    fake_model.edit = refusing  # type: ignore[method-assign]

    fixing.fixed(2, "new_picture", change=MUG_FIX, photos=[photo(1, "black mug")])
    steps.run_held()

    step = last_step("starting_picture")
    assert step.status == "failed"
    assert "the picture model refused to draw the black mug" in step.result
    assert "Ask them for another photo of the black mug" in step.result
    assert any("the picture model refused to draw the black mug" in told for told in errors())
    assert (
        "Scene 2's starting picture couldn't be made: your black mug couldn't be drawn from "
        "your photo. Please send another photo of it."
    ) in problems()


def test_a_scene_planned_before_b_roll_labels_cant_use_photos(
    fake_model: FakeModel, shown: ProducedItem, say: Say
) -> None:
    Scene.objects.filter(number=2).update(broll_kind="")
    photos_sent(fake_model, say, OWNER_MUG)

    plan = fixing.planned(
        fixing.plan_row(2, "new_picture", change=MUG_FIX, photos=[photo(1, "black mug")]),
        note=MUG_NOTE,
    )

    assert plan.scenes.get().cant == (
        "Scene 2 was planned before scenes had their B-roll labels, so your photos can't be "
        "used in it."
    )
    fixing.say_yes(plan)
    assert not Fix.objects.exists()


# --- The clean picture is drawn once -----------------------------------------------------------


def test_two_scenes_using_the_same_photo_share_one_clean_picture(
    fake_model: FakeModel, shown: ProducedItem, steps: HeldSteps, say: Say
) -> None:
    photos_sent(fake_model, say, OWNER_MUG)
    fake_model.respond("choose_broll_picture", BROLL_CHOICE, BROLL_CHOICE)
    plan = fixing.planned(
        fixing.plan_row(2, "new_picture", change=MUG_FIX, photos=[photo(1, "black mug")]),
        fixing.plan_row(
            3,
            "new_picture",
            change="In scene 3, the mug looks like the shop owner's black mug.",
            photos=[photo(1, "black mug")],
        ),
        note=MUG_NOTE,
    )
    row_2, row_3 = plan.scenes.order_by("scene__number")
    # The clean picture is counted once, for both scenes.
    assert row_2.estimate_usd - row_3.estimate_usd == CLEAN_PICTURE_USD

    fixing.say_yes(plan)
    steps.run_held()

    assert len(clean_pictures()) == 1
    assert {row.drawn for row in ReferencePhoto.objects.all()} == set(clean_pictures())


def test_the_same_thing_named_in_other_capitals_shares_its_clean_picture(
    fake_model: FakeModel, mug_fixed: Fix, steps: HeldSteps
) -> None:
    fix = "In scene 3, the mug looks like the shop owner's Black mug."
    plan = fixing.planned(
        fixing.plan_row(3, "new_picture", change=fix, photos=[photo(1, "Black mug")]),
        note="Scene 3 too.",
    )
    # A picture and its clip, with no clean picture to draw: the mug's is already drawn.
    assert plan.scenes.get().estimate_usd == Decimal("0.4235")
    fake_model.respond("choose_broll_picture", BROLL_CHOICE)

    fixing.say_yes(plan)
    steps.run_held()

    assert len(clean_pictures()) == 1
    assert handoffs("make_starting_picture")[-1]["pictures"][0] == clean_pictures()[0]


def test_a_clean_picture_drawn_before_a_restart_is_used_not_drawn_again(mug_fixed: Fix) -> None:
    from jobs.work import clean_picture

    (drawn,) = clean_pictures()
    # As if the worker stopped once the picture was paid for, before it was kept.
    ReferencePhoto.objects.update(drawn="")
    row = ReferencePhoto.objects.get()

    assert clean_picture(row.scene, row) == drawn
    assert clean_pictures() == [drawn]
    assert ReferencePhoto.objects.get().drawn == drawn


# --- The job's library (spec #210 point 1) ----------------------------------------------------


def test_the_shop_owners_photo_cleaned_after_the_plan_joins_the_library_at_the_next_number(
    mug_fixed: Fix,
) -> None:
    from jobs.library import OwnerPhoto, PagePhoto, library_of

    (clean,) = clean_pictures()
    job = Job.objects.get()
    page_photos = list(job.photos.order_by("position"))
    assert [(item.number, item.source, item.file) for item in library_of(job)] == [
        (1, PagePhoto(photo=1), page_photos[0].file),
        (2, PagePhoto(photo=2), page_photos[1].file),
        (3, OwnerPhoto(original=owner_file(1)), clean),
    ]
    # Labelled by the labelling call, shown the clean picture and told what it is of.
    labelling = ModelCall.objects.filter(purpose="label_photo").order_by("id").last()
    assert labelling is not None
    assert labelling.handoff["shows"] == "the shop owner's black mug"
    # Named by the photo it was drawn from and the picture drawn, so it pays once.
    assert labelling.handoff["photo"] == f"{owner_file(1)} as {clean}"
    assert [image["key"] for image in labelling.images] == [clean]
    assert library_of(job)[2].label == LABELLED["label"]


def test_a_clean_picture_drawn_again_after_a_restart_enters_the_library_once(
    mug_fixed: Fix,
) -> None:
    from jobs.library import library_of
    from jobs.work import clean_picture

    ReferencePhoto.objects.update(drawn="")
    row = ReferencePhoto.objects.get()

    clean_picture(row.scene, row)

    assert [item.number for item in library_of(Job.objects.get())] == [1, 2, 3]
