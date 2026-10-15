"""The shop owner's photos on notes (Reference photo, CONTEXT.md), driven through the chat: a
fix names one thing to take from each photo, code asks a model where it goes in each B-roll
scene, draws a clean picture of only that thing from the photo, and either draws the scene's
starting picture again with it, sent first and labelled to win, or makes its clip in
reference mode, the starting picture first and the clean picture after (Julian 2026-10-15,
handoffs/owner-photos-build-spec.md). The producer's model and the scene models are faked at
the gateway; the clips are real tiny videos and ffmpeg runs for real."""

from collections.abc import Callable
from decimal import Decimal
from typing import Any

import pytest

from agents.money import dollars
from agents.producer import CLEAN_PICTURE_USD
from gateway.fake import FakeModel, turn
from gateway.models import ModelCall
from gateway.types import BlockedBySafetyFilter, ModelRequest
from jobs import fix_check, reference_photos
from jobs.clip_judge import MOTION_PHOTO_FIX_QUESTION, OWNER_PHOTO_LABEL
from jobs.fix_check import PhotoFixVerdict
from jobs.models import Fix, Job, ProducedItem, ReferencePhoto, Scene, SceneStep
from jobs.scenes import SAME_SIDE

from .conftest import (
    CLIP_FIXED,
    CLIP_MOTION_OK,
    FACTS_OK,
    NO_CHOICES,
    OWNER_PHOTO_SHOWS,
    PICTURE_OK,
    PLAN,
    HeldSteps,
    broll,
    handoffs,
    paid_for,
    picture,
    results_of,
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
OWNER_SHELF = ("my-shelf.png", picture(60, 80, (90, 60, 30)))
OWNER_PLANT = ("my-plant.png", picture(60, 80, (20, 140, 40)))

MUG_NOTE = "Our mug is black, make it look like this one."
MUG_FIX = "In scene 2, the mug looks like the shop owner's black mug."
TEA_NOTE = "The tea should look like this, it's our own blend."
TEA_FIX = "In scene 2, the tea poured in looks like the shop owner's tea."

WHERE_SAYS = {
    "photo_shows": "A black stoneware mug.",
    "picture_shows": "A sage green mug on a workbench.",
    "why": "The mug is in the picture.",
}


def where(answer: str) -> dict[str, Any]:
    """The route call answering `answer`."""
    return {**WHERE_SAYS, "where": answer}


@pytest.fixture
def routes(fake_model: FakeModel) -> dict[tuple[str, str], str]:
    """Where the route call says each (thing, scene shows) goes: in the starting picture
    unless a test says otherwise. Every request it was sent is kept under "asked"."""
    answers: dict[tuple[str, str], str] = {}
    asked: list[ModelRequest[Any]] = []

    def answering(request: ModelRequest[Any]) -> dict[str, Any]:
        asked.append(request)
        handoff = request.handoff
        return where(answers.get((handoff.thing, handoff.scene_shows), "in_the_picture"))

    fake_model.answer_unscripted("route_reference_photo", answering)
    answers[("asked", "")] = ""  # keeps the dict's type; never a real thing
    routes.asked = asked  # type: ignore[attr-defined]
    return answers


def route_requests() -> list[dict[str, Any]]:
    return handoffs("route_reference_photo")


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
def shown(
    fake_model: FakeModel, checked: None, steps: HeldSteps, say: Say, routes: Any
) -> ProducedItem:
    """The first ad, made and shown through the chat."""
    calling(fake_model, say, ("create_music", {"mood": "light upbeat lo-fi"}))
    made_ready(fake_model, steps, say)
    calling(fake_model, say, *[("make_clip", {"scene": scene}) for scene in (1, 2, 3)])
    run(fake_model, steps)
    calling(fake_model, say, ("assemble_ad", {}))
    return Job.objects.get().produced.get(kind="finished_ad")


def fix_asked(
    scene: int,
    fix: str,
    redo: str,
    photos: list[tuple[int, str]] | None = None,
    drop: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "scene": scene,
        "fix": fix,
        "redo": redo,
        "photos": [{"photo": photo, "thing": thing} for photo, thing in photos or []] or None,
        "drop": drop,
    }


def restates(
    fake_model: FakeModel, say: Say, text: str, *fixes: dict[str, Any], photos: tuple[Any, ...] = ()
) -> str:
    """The shop owner sends `text` with `photos`; the producer restates `fixes` and replies.
    Gives what restate_fix handed back."""
    fake_model.respond(
        "produce",
        turn(calls=[("restate_fix", {"fixes": list(fixes)})]),
        turn(says="Here's what I'll change, and what it costs. Shall I?"),
    )
    say(text, *photos)
    return results_of("restate_fix")[-1]


def says_yes(
    fake_model: FakeModel, steps: HeldSteps, say: Say, *calls: tuple[str, dict[str, Any]]
) -> None:
    """The shop owner says yes; the producer makes `calls`, and the steps they start run."""
    fake_model.respond("produce", turn(calls=list(calls)), turn(says="On it."))
    say("Yes, go ahead.")
    run(fake_model, steps)


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
    return list(
        ReferencePhoto.objects.filter(scene__number=scene, dropped_by__isnull=True).values_list(
            "thing", "use"
        )
    )


# --- Restating: where each photo's thing goes -------------------------------------------------


def test_a_thing_in_the_starting_picture_is_restated_as_a_new_starting_picture(
    fake_model: FakeModel, shown: ProducedItem, say: Say, routes: dict[tuple[str, str], str]
) -> None:
    routes[("black mug", SHOWS_3)] = "appears_during_the_clip"
    picture_2 = latest("starting_picture")

    # The producer guessed a new clip; the route call finds the mug in the picture.
    said = restates(
        fake_model,
        say,
        MUG_NOTE,
        fix_asked(2, MUG_FIX, "clip", [(1, "the black mug")]),
        photos=(OWNER_MUG,),
    )

    fix = Fix.objects.get()
    assert fix.redo == "picture"
    assert fix.photos == [
        {
            "photo": 1,
            "file": owner_file(1),
            "thing": "black mug",
            "use": "picture",
            "where": "in_the_picture",
        }
    ]
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
    # The clean picture is counted once, in the estimate the shop owner is told.
    assert fix.estimate_usd > CLEAN_PICTURE_USD
    assert "Owner photo 1 is taken as the black mug, in scene 2." in said
    assert "never how their photos are used" in said
    # Nothing is drawn before their yes.
    assert clean_pictures() == []


def test_a_thing_that_only_shows_during_the_clip_is_restated_as_a_new_clip(
    fake_model: FakeModel, shown: ProducedItem, say: Say, routes: dict[tuple[str, str], str]
) -> None:
    routes[("tea", SHOWS)] = "appears_during_the_clip"
    routes[("tea", SHOWS_3)] = "appears_during_the_clip"

    restates(
        fake_model,
        say,
        TEA_NOTE,
        fix_asked(2, TEA_FIX, "picture", [(1, "tea")]),
        photos=(OWNER_TEA,),
    )

    fix = Fix.objects.get()
    # The producer said picture: its pick stands, and the tea goes to the clip.
    assert fix.redo == "picture"
    assert fix.photos[0]["use"] == "clip"


def test_a_thing_only_during_the_clip_with_a_clip_fix_stays_a_clip_fix(
    fake_model: FakeModel, shown: ProducedItem, say: Say, routes: dict[tuple[str, str], str]
) -> None:
    routes[("tea", SHOWS)] = "appears_during_the_clip"
    routes[("tea", SHOWS_3)] = "appears_during_the_clip"

    restates(
        fake_model, say, TEA_NOTE, fix_asked(2, TEA_FIX, "clip", [(1, "tea")]), photos=(OWNER_TEA,)
    )

    assert Fix.objects.get().redo == "clip"


def test_a_thing_to_add_from_the_start_is_drawn_into_the_starting_picture(
    fake_model: FakeModel, shown: ProducedItem, say: Say, routes: dict[tuple[str, str], str]
) -> None:
    # D6: sent to the clip, a still object would pop in mid-clip.
    routes[("plant", SHOWS)] = "belongs_from_the_start"
    routes[("plant", SHOWS_3)] = "belongs_from_the_start"

    restates(
        fake_model,
        say,
        "Put this plant on the workbench.",
        fix_asked(
            2, "In scene 2, the shop owner's plant stands on the workbench.", "clip", [(1, "plant")]
        ),
        photos=(OWNER_PLANT,),
    )

    fix = Fix.objects.get()
    assert (fix.redo, fix.photos[0]["use"]) == ("picture", "picture")
    # Scene 3's picture doesn't show it, and the shop owner didn't name scene 3.
    assert not Fix.objects.filter(scene__number=3).exists()


def test_with_no_answer_from_the_route_call_the_producers_pick_stands(
    fake_model: FakeModel, shown: ProducedItem, say: Say
) -> None:
    from gateway.types import UnusableReply

    fake_model.respond(
        "route_reference_photo", *[UnusableReply("unreadable", input_tokens=1, output_tokens=1)] * 2
    )

    restates(
        fake_model, say, TEA_NOTE, fix_asked(2, TEA_FIX, "clip", [(1, "tea")]), photos=(OWNER_TEA,)
    )

    fix = Fix.objects.get()
    assert (fix.redo, fix.photos[0]["use"], fix.photos[0]["where"]) == (
        "clip",
        "clip",
        "not answered",
    )


# --- Scenes the shop owner didn't name -------------------------------------------------------


def test_another_scene_showing_the_thing_joins_the_fix_with_its_cost(
    fake_model: FakeModel, shown: ProducedItem, say: Say, routes: dict[tuple[str, str], str]
) -> None:
    said = restates(
        fake_model,
        say,
        MUG_NOTE,
        fix_asked(2, MUG_FIX, "picture", [(1, "black mug")]),
        photos=(OWNER_MUG,),
    )

    fix_2, fix_3 = Fix.objects.order_by("scene__number")
    joined = "Make the black mug look like the one in the shop owner's photo."
    assert (fix_3.fix, fix_3.redo, fix_3.joined) == (joined, "picture", True)
    assert fix_3.photos[0]["thing"] == "black mug"
    # Scene 3's route call is about a scene the shop owner didn't name.
    assert handoffs("route_reference_photo")[1]["change"] is None
    assert "Owner photo 1 is taken as the black mug, in scenes 2 and 3." in said
    assert f'so a fix was added for it: "{joined}"' in said
    assert f"Scene 3: about {dollars(fix_3.estimate_usd)}" in said
    # The clean picture is counted once, for both scenes.
    assert fix_2.estimate_usd - fix_3.estimate_usd == CLEAN_PICTURE_USD


def test_a_scene_whose_picture_doesnt_show_the_thing_is_left_out(
    fake_model: FakeModel, shown: ProducedItem, say: Say, routes: dict[tuple[str, str], str]
) -> None:
    for answer in ("belongs_from_the_start", "appears_during_the_clip"):
        routes[("black mug", SHOWS_3)] = answer
        restates(
            fake_model,
            say,
            MUG_NOTE,
            fix_asked(2, MUG_FIX, "picture", [(1, "black mug")]),
            photos=(OWNER_MUG,),
        )

    assert set(Fix.objects.values_list("scene__number", flat=True)) == {2}


def test_scenes_the_shop_owner_names_each_get_the_photo(
    fake_model: FakeModel, shown: ProducedItem, say: Say, routes: dict[tuple[str, str], str]
) -> None:
    routes[("black mug", SHOWS_3)] = "belongs_from_the_start"

    restates(
        fake_model,
        say,
        "Scenes 2 and 3: our mug is black, like this.",
        fix_asked(2, MUG_FIX, "picture", [(1, "black mug")]),
        fix_asked(
            3,
            "In scene 3, the mug looks like the shop owner's black mug.",
            "clip",
            [(1, "black mug")],
        ),
        photos=(OWNER_MUG,),
    )

    fixes = list(Fix.objects.order_by("scene__number").values_list("redo", "joined"))
    assert fixes == [("picture", False), ("picture", False)]


def test_a_correction_drops_the_fixes_code_added_to_the_restatement_it_corrects(
    fake_model: FakeModel, shown: ProducedItem, say: Say, routes: dict[tuple[str, str], str]
) -> None:
    restates(
        fake_model,
        say,
        MUG_NOTE,
        fix_asked(2, MUG_FIX, "picture", [(1, "black mug")]),
        photos=(OWNER_MUG,),
    )
    assert Fix.objects.filter(scene__number=3, joined=True).exists()
    routes[("mug handle", SHOWS_3)] = "belongs_from_the_start"

    # Case 13: "the handle, not the whole mug": scene 3 no longer shows it.
    restates(
        fake_model,
        say,
        "Only the handle, not the whole mug.",
        fix_asked(
            2, "In scene 2, the handle looks like the shop owner's.", "picture", [(1, "mug handle")]
        ),
    )

    assert not Fix.objects.filter(scene__number=3).exists()
    assert Fix.objects.filter(scene__number=2).last().photos[0]["thing"] == "mug handle"  # type: ignore[union-attr]


# --- What restate_fix refuses ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("asked", "refused"),
    [
        (
            fix_asked(1, "In scene 1, she holds the black mug.", "picture", [(1, "black mug")]),
            "scene 1 shows the person talking, and the shop owner's photos can't be used in a "
            "scene where the person talks yet. Tell them so, and offer what you can change in "
            "it instead.",
        ),
        (
            fix_asked(
                2,
                'Scene 2 says "Black and lovely, holds 350 ml, dishwasher safe."',
                "line",
                [(1, "black mug")],
            ),
            "scene 2's fix is a new line, and a photo changes how something looks, not what is "
            "said. Restate the line and the photo as separate changes, or leave the photo out.",
        ),
        (
            fix_asked(2, MUG_FIX, "picture", [(2, "black mug")]),
            "the chat has no owner photo 2: the shop owner's only photo is 1.",
        ),
        (
            fix_asked(2, MUG_FIX, "picture", [(1, "black mug"), (1, "the Black  mug")]),
            "scene 2's fix takes the black mug from more than one photo. Ask the shop owner "
            "which photo shows it.",
        ),
        (
            fix_asked(2, MUG_FIX, "picture", [(1, "a")]),
            "scene 2's fix names no thing to take from one of its photos. Name one from what "
            "the shop owner said, or ask them.",
        ),
        (
            fix_asked(2, MUG_FIX, "picture", [(1, "mug"), (1, "tea"), (1, "shelf"), (1, "plant")]),
            "scene 2's fix takes things from 4 photos, and a note's fix takes them from at most "
            "3. Ask the shop owner which 3 to use.",
        ),
        (
            fix_asked(2, "Go back to the green mug.", "picture", drop=["black mug"]),
            "scene 2 uses no photo of the black mug. The shop owner's photos it uses: none.",
        ),
    ],
    ids=["talking scene", "line", "no such photo", "thing twice", "no thing", "4 photos", "drop"],
)
def test_restate_fix_refuses_photos_it_cant_use(
    fake_model: FakeModel, shown: ProducedItem, say: Say, asked: dict[str, Any], refused: str
) -> None:
    restates(fake_model, say, MUG_NOTE, asked, photos=(OWNER_MUG,))

    assert results_of("restate_fix")[-1] == f"Refused: {refused} Nothing was done."
    assert not Fix.objects.exists()


# --- A new starting picture with the photo -----------------------------------------------------


@pytest.fixture
def mug_fixed(
    fake_model: FakeModel,
    shown: ProducedItem,
    steps: HeldSteps,
    say: Say,
    routes: dict[tuple[str, str], str],
) -> Fix:
    """Scene 2 drawn again with the shop owner's black mug, and its clip made from it."""
    routes[("black mug", SHOWS_3)] = "appears_during_the_clip"
    restates(
        fake_model,
        say,
        MUG_NOTE,
        fix_asked(2, MUG_FIX, "picture", [(1, "black mug")]),
        photos=(OWNER_MUG,),
    )
    fake_model.respond("choose_broll_picture", BROLL_CHOICE)
    says_yes(fake_model, steps, say, ("make_starting_picture", {"scene": 2, "note": MUG_FIX}))
    calling(fake_model, say, ("make_clip", {"scene": 2, "note": None}))
    run(fake_model, steps)
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
    # The clip of the new picture is made from it, as any clip: the mug is in its picture.
    sent = handoffs("make_broll_clip")[-1]
    assert sent["starting_picture"] == latest("starting_picture").file
    assert sent["example_pictures"] == []


def test_the_writer_of_the_prompts_is_told_the_shop_owners_pictures_win(
    fake_model: FakeModel,
    shown: ProducedItem,
    steps: HeldSteps,
    say: Say,
    routes: dict[tuple[str, str], str],
    instructed: dict[str, list[str]],  # noqa: F811 (the fixture)
) -> None:
    routes[("black mug", SHOWS_3)] = "appears_during_the_clip"
    restates(
        fake_model,
        say,
        MUG_NOTE,
        fix_asked(2, MUG_FIX, "picture", [(1, "black mug")]),
        photos=(OWNER_MUG,),
    )
    fake_model.respond("choose_broll_picture", BROLL_CHOICE)
    says_yes(fake_model, steps, say, ("make_starting_picture", {"scene": 2, "note": MUG_FIX}))

    assert instructed["choose_broll_picture"][-1].endswith(
        reference_photos.writer_rules(["black mug"], [])
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


def test_no_photo_is_kept_before_the_shop_owner_says_yes(
    fake_model: FakeModel, shown: ProducedItem, say: Say, routes: Any
) -> None:
    restates(
        fake_model,
        say,
        MUG_NOTE,
        fix_asked(2, MUG_FIX, "picture", [(1, "black mug")]),
        photos=(OWNER_MUG,),
    )

    assert not ReferencePhoto.objects.exists()


# --- Reference mode ------------------------------------------------------------------------------


REWRITE: dict[str, Any] = {
    "motion_prompt": "Tea pours from the teapot into the mug.",
    "motion_prompt_reason": "The note is about the tea.",
}


@pytest.fixture
def tea_fixed(
    fake_model: FakeModel,
    shown: ProducedItem,
    steps: HeldSteps,
    say: Say,
    routes: dict[tuple[str, str], str],
) -> Fix:
    """Scene 2's clip made again in reference mode with the shop owner's tea."""
    routes[("tea", SHOWS)] = "appears_during_the_clip"
    routes[("tea", SHOWS_3)] = "appears_during_the_clip"
    restates(
        fake_model, say, TEA_NOTE, fix_asked(2, TEA_FIX, "clip", [(1, "tea")]), photos=(OWNER_TEA,)
    )
    fake_model.respond("rewrite_clip_prompt", REWRITE)
    says_yes(fake_model, steps, say, ("make_clip", {"scene": 2, "note": TEA_FIX}))
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


def test_the_clip_prompt_writer_is_told_of_the_shop_owners_thing_and_shown_it(
    tea_fixed: Fix,
) -> None:
    call = ModelCall.objects.filter(purpose="rewrite_clip_prompt").last()
    assert call is not None
    assert call.images[1] == {"label": "The shop owner's own tea", "key": clean_pictures()[0]}


def test_the_clip_judge_is_shown_the_shop_owners_photo_for_the_change(tea_fixed: Fix) -> None:
    motion = ModelCall.objects.filter(purpose="check_clip_motion").order_by("-id")[:3]
    for call in motion:
        assert call.handoff["owner_photos"] == ["tea"]
        assert {"label": OWNER_PHOTO_LABEL.format("tea"), "key": owner_file(1)} in call.images
    # SIDES is judged as any clip, without the photo.
    sides = ModelCall.objects.filter(purpose="check_clip_sides").order_by("-id")[:3]
    assert all("owner_photos" not in call.handoff for call in sides)
    check = tea_fixed.checks.get()
    assert check.verdict == "passed"
    assert MOTION_PHOTO_FIX_QUESTION


def test_a_remake_after_the_judge_fails_stays_in_reference_mode(
    fake_model: FakeModel,
    shown: ProducedItem,
    steps: HeldSteps,
    say: Say,
    routes: dict[tuple[str, str], str],
) -> None:

    routes[("tea", SHOWS)] = "appears_during_the_clip"
    routes[("tea", SHOWS_3)] = "appears_during_the_clip"
    restates(
        fake_model, say, TEA_NOTE, fix_asked(2, TEA_FIX, "clip", [(1, "tea")]), photos=(OWNER_TEA,)
    )
    failing = {
        **CLIP_MOTION_OK,
        "real_motion": {"verdict": "fail", "problem": "The tea floats.", "back_to_normal_at": None},
    }
    fixed = {"owner_photo_shows": OWNER_PHOTO_SHOWS, **CLIP_FIXED}
    fake_model.respond("check_clip_motion", *[{**failing, "owner_change": fixed}] * 3)
    fake_model.respond("rewrite_clip_prompt", REWRITE)
    fake_model.respond("remake_clip_prompt", REWRITE)
    says_yes(fake_model, steps, say, ("make_clip", {"scene": 2, "note": TEA_FIX}))

    remade = handoffs("make_broll_clip")[-1]
    assert remade["starting_picture"] is None
    assert len(remade["example_pictures"]) == 2
    assert remade["prompt"].startswith("Image 1 is how the clip starts")


# --- Several photos on one note ------------------------------------------------------------------


def test_one_note_with_two_photos_takes_one_thing_from_each(
    fake_model: FakeModel,
    shown: ProducedItem,
    steps: HeldSteps,
    say: Say,
    routes: dict[tuple[str, str], str],
) -> None:
    routes[("black mug", SHOWS_3)] = "appears_during_the_clip"
    routes[("tea", SHOWS)] = "appears_during_the_clip"
    routes[("tea", SHOWS_3)] = "appears_during_the_clip"
    fix = "In scene 2, the mug is the shop owner's black mug and the tea is their own blend."

    said = restates(
        fake_model,
        say,
        "Photo 1 is our mug and photo 2 is our tea.",
        fix_asked(2, fix, "clip", [(1, "black mug"), (2, "tea")]),
        photos=(OWNER_MUG, OWNER_TEA),
    )
    assert "Owner photo 1 is taken as the black mug" in said
    assert "Owner photo 2 is taken as the tea" in said
    assert Fix.objects.get(scene__number=2).redo == "picture"
    fake_model.respond("choose_broll_picture", BROLL_CHOICE)
    says_yes(fake_model, steps, say, ("make_starting_picture", {"scene": 2, "note": fix}))
    calling(fake_model, say, ("make_clip", {"scene": 2, "note": None}))
    run(fake_model, steps)

    mug, tea = clean_pictures()
    # The mug is drawn into the picture; the tea only goes to the clip.
    assert handoffs("make_starting_picture")[-1]["pictures"][0] == mug
    assert tea not in handoffs("make_starting_picture")[-1]["pictures"]
    sent = handoffs("make_broll_clip")[-1]
    assert sent["example_pictures"] == [latest("starting_picture").file, tea]
    assert sorted(in_use()) == [("black mug", "picture"), ("tea", "clip")]


def test_a_note_per_photo_restated_together_takes_each_photo_by_its_number(
    fake_model: FakeModel, shown: ProducedItem, say: Say, routes: dict[tuple[str, str], str]
) -> None:
    routes[("black mug", SHOWS_3)] = "appears_during_the_clip"
    fake_model.respond("produce", turn(says="And the other photo?"))
    say("This is our mug.", OWNER_MUG)

    restates(
        fake_model,
        say,
        "And this is our tea.",
        fix_asked(
            2,
            "In scene 2, the black mug and their own tea.",
            "picture",
            [(1, "black mug"), (2, "tea")],
        ),
        photos=(OWNER_TEA,),
    )

    files = [photo["file"] for photo in Fix.objects.get(scene__number=2).photos]
    assert files == [owner_file(1), owner_file(2)]


# --- The photo stays with the scene (D1) --------------------------------------------------------


def test_a_later_fix_on_the_scene_draws_it_with_the_photo_again_without_drawing_it_again(
    fake_model: FakeModel, mug_fixed: Fix, steps: HeldSteps, say: Say
) -> None:
    brighter = "In scene 2, the workshop is brighter."
    restates(fake_model, say, "Make it brighter.", fix_asked(2, brighter, "picture"))
    fake_model.respond("choose_broll_picture", BROLL_CHOICE)
    says_yes(fake_model, steps, say, ("make_starting_picture", {"scene": 2, "note": brighter}))

    assert len(clean_pictures()) == 1
    assert handoffs("make_starting_picture")[-1]["pictures"][0] == clean_pictures()[0]
    assert in_use() == [("black mug", "picture")]
    # The check of that fix has no photo of its own: it is asked as any.
    assert "owner_photos" not in handoffs("check_fix")[-1]


def test_a_new_line_remakes_the_scene_with_the_photo(
    fake_model: FakeModel, mug_fixed: Fix, steps: HeldSteps, say: Say
) -> None:
    line = 'Scene 2 says "Black stoneware, holds 350 ml, dishwasher safe."'
    restates(fake_model, say, "Change scene 2's line.", fix_asked(2, line, "line"))
    fake_model.respond("choose_broll_picture", BROLL_CHOICE)
    says_yes(
        fake_model,
        steps,
        say,
        ("change_line", {"scene": 2, "line": "Black stoneware, holds 350 ml, dishwasher safe."}),
        ("make_starting_picture", {"scene": 2, "note": None}),
    )

    assert last_step("starting_picture").note == ""
    assert handoffs("make_starting_picture")[-1]["pictures"][0] == clean_pictures()[0]


# --- Going back, a new photo of the same thing, and how many photos a scene uses ---------------


def test_going_back_brings_back_the_picture_and_clip_from_before_the_photo_for_nothing(
    fake_model: FakeModel, mug_fixed: Fix, steps: HeldSteps, say: Say
) -> None:
    before = ProducedItem.objects.get(kind="starting_picture", scene__number=2, version=1)
    old_clip = ProducedItem.objects.get(kind="clip", scene__number=2, version=1)
    back = "In scene 2, the mug is the green one again, as before the shop owner's photo."

    said = restates(
        fake_model,
        say,
        "Go back to the green mug.",
        fix_asked(2, back, "picture", drop=["black mug"]),
    )

    fix = Fix.objects.last()
    assert fix is not None and fix.brings_back == before
    assert fix.estimate_usd == 0
    assert "Scene 2 goes back to its starting picture from before the photo" in said
    paid = paid_for()
    says_yes(fake_model, steps, say, ("make_starting_picture", {"scene": 2, "note": back}))
    calling(fake_model, say, ("make_clip", {"scene": 2, "note": None}))
    run(fake_model, steps)

    assert paid_for() == paid
    assert latest("starting_picture").file == before.file
    assert latest("clip").file == old_clip.file
    # D5: the scene no longer uses the photo, so the next fix won't bring it back.
    assert in_use() == []


def test_going_back_after_a_later_change_draws_it_again_without_the_photo_and_says_so(
    fake_model: FakeModel, mug_fixed: Fix, steps: HeldSteps, say: Say
) -> None:
    brighter = "In scene 2, the workshop is brighter."
    restates(fake_model, say, "Make it brighter.", fix_asked(2, brighter, "picture"))
    fake_model.respond("choose_broll_picture", BROLL_CHOICE)
    says_yes(fake_model, steps, say, ("make_starting_picture", {"scene": 2, "note": brighter}))
    back = "In scene 2, the mug is the green one again, and the workshop stays brighter."

    said = restates(
        fake_model,
        say,
        "Go back to the green mug.",
        fix_asked(2, back, "picture", drop=["black mug"]),
    )

    assert Fix.objects.last().brings_back is None  # type: ignore[union-attr]
    assert (
        "can't simply go back to how it was before the photo: the shop owner said yes to a "
        f'change since ("{brighter}")' in said
    )
    fake_model.respond("choose_broll_picture", BROLL_CHOICE)
    says_yes(fake_model, steps, say, ("make_starting_picture", {"scene": 2, "note": back}))

    assert clean_pictures()[0] not in handoffs("make_starting_picture")[-1]["pictures"]
    assert in_use() == []


def test_a_new_photo_of_the_same_thing_replaces_the_old_one(
    fake_model: FakeModel,
    mug_fixed: Fix,
    steps: HeldSteps,
    say: Say,
    routes: dict[tuple[str, str], str],
) -> None:
    other = ("other-mug.png", picture(60, 80, (5, 5, 40)))
    fix = "In scene 2, the mug looks like the shop owner's new photo of their black mug."
    restates(
        fake_model,
        say,
        "Use this photo of our mug instead.",
        fix_asked(2, fix, "picture", [(2, "the Black mug")]),
        photos=(other,),
    )
    fake_model.respond("choose_broll_picture", BROLL_CHOICE)
    says_yes(fake_model, steps, say, ("make_starting_picture", {"scene": 2, "note": fix}))

    rows = ReferencePhoto.objects.filter(scene__number=2).order_by("id")
    old, new = rows
    assert old.dropped_by == new.added_by
    assert (new.file, new.dropped_by) == (owner_file(2), None)
    assert handoffs("make_starting_picture")[-1]["pictures"][0] == clean_pictures()[-1]


def test_another_scene_using_a_photo_of_the_same_thing_joins_when_it_is_replaced(
    fake_model: FakeModel,
    tea_fixed: Fix,
    steps: HeldSteps,
    say: Say,
    routes: dict[tuple[str, str], str],
) -> None:
    # Scene 2 uses the tea during its clip; a new tea photo noted on scene 3 replaces it there
    # too, though scene 2's picture doesn't show the tea.
    other = ("other-tea.png", picture(60, 80, (200, 120, 40)))
    said = restates(
        fake_model,
        say,
        "Our tea looks like this now.",
        fix_asked(
            3, "In scene 3, the tea looks like the shop owner's new photo.", "clip", [(2, "tea")]
        ),
        photos=(other,),
    )

    joined = Fix.objects.get(scene__number=2, joined=True)
    assert joined.photos[0]["use"] == "clip"
    assert joined.redo == "clip"
    assert "Scene 2 shows the tea too" in said


def test_a_fourth_photo_on_a_scene_asks_which_to_drop(
    fake_model: FakeModel,
    mug_fixed: Fix,
    steps: HeldSteps,
    say: Say,
    routes: dict[tuple[str, str], str],
) -> None:
    fix = "In scene 2, the shelf, the tea and the plant are the shop owner's."
    restates(
        fake_model,
        say,
        "Use these too.",
        fix_asked(2, fix, "picture", [(2, "tea"), (3, "shelf"), (4, "plant")]),
        photos=(OWNER_TEA, OWNER_SHELF, OWNER_PLANT),
    )

    assert results_of("restate_fix")[-1] == (
        "Refused: scene 2 would use 4 of the shop owner's photos (the black mug, the tea, the "
        "shelf and the plant), and a scene uses at most 3. Ask them which to stop using, and "
        "restate the fix with it to drop. Nothing was done."
    )

    restates(
        fake_model,
        say,
        "Drop the mug one.",
        fix_asked(2, fix, "picture", [(2, "tea"), (3, "shelf"), (4, "plant")], drop=["black mug"]),
    )

    asked = Fix.objects.filter(scene__number=2, joined=False).last()
    assert asked is not None
    assert (asked.drops, [photo["thing"] for photo in asked.photos]) == (
        ["black mug"],
        ["tea", "shelf", "plant"],
    )


# --- A photo that can't be drawn clean (D8) -----------------------------------------------------


def test_a_clean_picture_that_keeps_failing_its_check_asks_for_another_photo(
    fake_model: FakeModel, shown: ProducedItem, steps: HeldSteps, say: Say, routes: Any
) -> None:
    routes[("black mug", SHOWS_3)] = "appears_during_the_clip"
    restates(
        fake_model,
        say,
        MUG_NOTE,
        fix_asked(2, MUG_FIX, "picture", [(1, "black mug")]),
        photos=(OWNER_MUG,),
    )
    fake_model.respond("check_starting_picture", *[impossible(FUSED_HANDLE)] * 6)
    old = latest("clip")

    says_yes(fake_model, steps, say, ("make_starting_picture", {"scene": 2, "note": MUG_FIX}))

    step = last_step("starting_picture")
    assert step.status == "failed"
    assert len(clean_pictures()) == 3
    assert not handoffs("choose_broll_picture")[3:]
    assert "Ask them for another photo of the black mug" in step.result
    assert latest("clip") == old
    assert Scene.objects.get(number=2).status == "finished"


def test_a_photo_the_picture_model_refuses_asks_for_another_photo(
    fake_model: FakeModel, shown: ProducedItem, steps: HeldSteps, say: Say, routes: Any
) -> None:
    routes[("black mug", SHOWS_3)] = "appears_during_the_clip"
    restates(
        fake_model,
        say,
        MUG_NOTE,
        fix_asked(2, MUG_FIX, "picture", [(1, "black mug")]),
        photos=(OWNER_MUG,),
    )
    original = fake_model.edit

    def refusing(**asked: Any) -> Any:
        if asked["prompt"].startswith("A clean photo of only"):
            raise BlockedBySafetyFilter("refused")
        return original(**asked)

    fake_model.edit = refusing  # type: ignore[method-assign]
    says_yes(fake_model, steps, say, ("make_starting_picture", {"scene": 2, "note": MUG_FIX}))

    step = last_step("starting_picture")
    assert step.status == "failed"
    assert "the picture model refused to draw the black mug" in step.result
    assert "Ask them for another photo of the black mug" in step.result


# --- The clean picture is drawn once -----------------------------------------------------------


def test_two_scenes_using_the_same_photo_share_one_clean_picture(
    fake_model: FakeModel, shown: ProducedItem, steps: HeldSteps, say: Say, routes: Any
) -> None:
    restates(
        fake_model,
        say,
        MUG_NOTE,
        fix_asked(2, MUG_FIX, "picture", [(1, "black mug")]),
        photos=(OWNER_MUG,),
    )
    joined = Fix.objects.get(scene__number=3).fix
    fake_model.respond("choose_broll_picture", BROLL_CHOICE, BROLL_CHOICE)
    says_yes(
        fake_model,
        steps,
        say,
        ("make_starting_picture", {"scene": 2, "note": MUG_FIX}),
        ("make_starting_picture", {"scene": 3, "note": joined}),
    )

    assert len(clean_pictures()) == 1
    assert {row.drawn for row in ReferencePhoto.objects.all()} == set(clean_pictures())
    assert Decimal(0) < CLEAN_PICTURE_USD


def test_the_route_call_is_answered_from_its_record_when_restated_again(
    fake_model: FakeModel, shown: ProducedItem, say: Say, routes: Any
) -> None:
    for _ in range(2):
        restates(
            fake_model,
            say,
            MUG_NOTE,
            fix_asked(2, MUG_FIX, "picture", [(1, "black mug")]),
            photos=(OWNER_MUG,),
        )

    paid = ModelCall.objects.filter(purpose="route_reference_photo")
    # Scenes 2 and 3, once each.
    assert paid.count() == 2
    assert fix_check.PHOTO_FIX_QUESTION
    assert PICTURE_OK
