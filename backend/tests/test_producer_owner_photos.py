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
from jobs import reference_photos
from jobs.clip_judge import (
    ASKS,
    MOTION_FIX_QUESTION,
    MOTION_FROM_REFERENCES,
    MOTION_PHOTO_FIX_QUESTION,
    OWNER_PHOTO_LABEL,
    wording_version,
)
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

from . import fixing
from .agent_tries import agent_films
from .conftest import (
    CLIP_FIXED,
    CLIP_MOTION_OK,
    CUT_OUT_OK,
    FACTS_OK,
    LABELLED,
    NO_CHOICES,
    OWNER_PHOTO_SHOWS,
    PLAN,
    HeldSteps,
    broll,
    handoffs,
    motion_answer,
    paid_for,
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
    """A later new picture of scene 2, with no photo, said yes to and made: a new clip, as the
    scene shows the product and has no starting picture (spec #210 point 19, #242)."""
    fake_model.respond("rewrite_clip_prompt", REWRITE)
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


def test_the_route_call_looks_at_the_photo_and_is_lenient_about_it() -> None:
    fields = list(reference_photos.RouteAnswer.model_fields)
    assert fields[:3] == ["photo_shows", "clear_enough", "unclear_why"]
    instructions = " ".join(reference_photos.ROUTE_INSTRUCTIONS.split())
    assert "An ordinary phone photo is clear enough" in instructions
    assert "answer no only when you can't really see the thing" in instructions


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


# --- A new picture with the photo: a new clip sent it (spec #210 point 19, #242) ---------------


@pytest.fixture
def mug_fixed(fake_model: FakeModel, shown: ProducedItem, steps: HeldSteps, say: Say) -> Fix:
    """Scene 2's new picture with the shop owner's black mug: the scene shows the product, so
    its clip is made again from the note, sent the clean picture of the mug."""
    photos_sent(fake_model, say, OWNER_MUG)
    fake_model.respond("rewrite_clip_prompt", REWRITE)
    fixing.fixed(2, "new_picture", note=MUG_NOTE, change=MUG_FIX, photos=[photo(1, "black mug")])
    steps.run_held()
    return Fix.objects.get()


def test_a_new_picture_with_the_shop_owners_photo_sends_its_clean_picture_with_the_clip(
    mug_fixed: Fix,
) -> None:
    (clean,) = clean_pictures()
    sent = handoffs("make_broll_clip")[-1]
    assert sent["starting_picture"] is None
    assert clean in sent["example_pictures"] and owner_file(1) not in sent["example_pictures"]
    assert not ProducedItem.objects.filter(kind="starting_picture", scene__number=2).exists()
    assert mug_fixed.redo == "clip"


def test_a_shop_owners_photo_the_clean_pictures_check_refuses_is_named_as_theirs(
    fake_model: FakeModel, shown: ProducedItem, steps: HeldSteps, say: Say
) -> None:
    """Requirement 22: the clean picture is checked without the shop owner's photo it was
    drawn from, and the red notice names that photo as theirs, not a shop photo."""

    def refusing(request: ModelRequest[Any]) -> dict[str, Any]:
        if owner_file(1) in [image.key for image in request.images]:
            raise BlockedBySafetyFilter("content_policy_violation")
        return CUT_OUT_OK

    fake_model.answer_unscripted("check_cut_out", refusing)
    photos_sent(fake_model, say, OWNER_MUG)
    fake_model.respond("rewrite_clip_prompt", REWRITE)
    fixing.fixed(2, "new_picture", note=MUG_NOTE, change=MUG_FIX, photos=[photo(1, "black mug")])
    steps.run_held()

    assert last_step("clip").status == "finished"
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


def test_a_thing_during_the_clip_is_sent_with_the_clips_pictures_as_its_clean_picture(
    tea_fixed: Fix,
) -> None:
    (clean,) = clean_pictures()
    sent = handoffs("make_broll_clip")[-1]
    # No starting picture (spec #210 point 19): the clip's sent list, the clean picture of the
    # tea among its pictures, never the raw photo; the prompt the rewrite wrote from the note.
    assert sent["starting_picture"] is None
    assert clean in sent["example_pictures"]
    assert owner_file(1) not in sent["example_pictures"]
    assert sent["prompt"] == REWRITE["motion_prompt"]
    assert not ProducedItem.objects.filter(kind="starting_picture", scene__number=2).exists()
    assert in_use() == [("tea", "clip")]


def test_the_clip_prompt_writer_is_told_of_the_shop_owners_thing_and_shown_it(
    tea_fixed: Fix,
) -> None:
    call = ModelCall.objects.filter(purpose="rewrite_clip_prompt").last()
    assert call is not None
    # The owner's thing is one of the clip's sent pictures (spec #210 point 19), shown with them.
    assert clean_pictures()[0] in [image["key"] for image in call.images]


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
    without_photo = f"{MOTION_FROM_REFERENCES}\n\n{MOTION_FIX_QUESTION}"
    assert instructed["check_clip_motion"][-1] == without_photo
    new = latest("clip")
    assert new.ruling is not None
    assert new.ruling["wording"] == wording_version(fix=True, sent=True)
    assert "owner_photo_shows" not in new.ruling["answers"]["motion"][0]["owner_change"]
    fix = Fix.objects.get()
    assert fix.checks.get().verdict == "passed"
    (said,) = [text for text in problems() if "safety filter" in text]
    assert "the shop owner's photo of their tea" in said and "scene 2's clip" in said


def test_a_try_after_the_judge_fails_still_sends_the_shop_owners_thing(
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
    assert clean_pictures()[0] in remade["example_pictures"]


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


# --- The photo stays with the scene (D1) --------------------------------------------------------


def test_a_later_fix_on_the_scene_draws_it_with_the_photo_again_without_drawing_it_again(
    fake_model: FakeModel, mug_fixed: Fix, steps: HeldSteps
) -> None:
    brighter(fake_model, steps)

    assert len(clean_pictures()) == 1
    assert clean_pictures()[0] in handoffs("make_broll_clip")[-1]["example_pictures"]
    assert in_use() == [("black mug", "picture")]


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
    fake_model.respond("rewrite_clip_prompt", REWRITE)

    fixing.fixed(2, "new_picture", change=fix, photos=[photo(2, "the Black mug")])
    steps.run_held()

    rows = ReferencePhoto.objects.filter(scene__number=2).order_by("id")
    old, new = rows
    assert old.dropped_by == new.added_by
    assert (new.file, new.dropped_by) == (owner_file(2), None)
    sent = handoffs("make_broll_clip")[-1]["example_pictures"]
    assert clean_pictures()[-1] in sent and clean_pictures()[0] not in sent


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
    made = latest("clip")
    paid = paid_for()

    plan = fixing.fixed(
        2, "new_picture", note="Again, please.", change=MUG_FIX, photos=[photo(1, "Black Mug")]
    )
    steps.run_held()

    assert plan.scenes.get().cant == (
        "Scene 2 already uses owner photo 1 for the Black Mug, so nothing would change."
    )
    assert paid_for() == paid
    assert latest("clip") == made


# --- A photo that can't be drawn clean (D8) -----------------------------------------------------


def test_a_clean_picture_that_keeps_failing_its_check_asks_for_another_photo(
    fake_model: FakeModel, shown: ProducedItem, steps: HeldSteps, say: Say
) -> None:
    photos_sent(fake_model, say, OWNER_MUG)
    # The cut-out check is asked once per clean picture: three fail it.
    fake_model.respond("check_cut_out", *[CUT_OUT_FAILS] * 3)
    old = latest("clip")

    fixing.fixed(2, "new_picture", change=MUG_FIX, photos=[photo(1, "black mug")])
    steps.run_held()

    step = last_step("clip")
    assert step.status == "failed"
    assert len(clean_pictures()) == 3
    # Scenes 2 and 3's first clips only: none was asked for the fix.
    assert len(handoffs("make_broll_clip")) == 2
    assert "Ask them for another photo of the black mug" in step.result
    assert any("Ask them for another photo of the black mug" in told for told in errors())
    assert latest("clip").file == old.file
    assert (
        "Scene 2's clip couldn't be made: your black mug couldn't be drawn from "
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

    step = last_step("clip")
    assert step.status == "failed"
    assert "the picture model refused to draw the black mug" in step.result
    assert "Ask them for another photo of the black mug" in step.result
    assert any("the picture model refused to draw the black mug" in told for told in errors())
    assert (
        "Scene 2's clip couldn't be made: your black mug couldn't be drawn from "
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
