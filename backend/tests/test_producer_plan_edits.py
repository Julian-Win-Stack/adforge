"""The producer edits the plan itself (spec #247 points 5 and 9; ticket #254): any field of
any scene, a scene added, removed or moved. Code marks the steps the edit made stale and
says so in the tool's answer; nothing is made again until the producer calls the scene
tools, and a scene tool asked for a step whose inputs are unchanged hands back what exists.
Driven through the chat from an ad already shown; the producer is scripted and every model
faked at the gateway; the clips are real tiny videos."""

from collections.abc import Callable
from datetime import timedelta
from typing import Any

import pytest
from django.db.models import Sum
from django.utils import timezone

from agents import tasks
from agents.models import ToolCall
from agents.money import dollars
from chat.models import Attachment
from gateway.fake import FakeModel, turn
from gateway.models import ModelCall
from jobs import notices, plan_edits, stale
from jobs.models import AgentFix, Job, ProducedItem, Scene, SceneStep
from jobs.plan_rules import (
    BOX_NEEDS_PACKAGING,
    DIFFERENT_STYLE_AND_STATE,
    FIRST_IS_TALKING,
    LAST_IS_TALKING,
    LENS_FIRST_OR_LAST,
    MIDDLE_IS_BROLL,
    RULES,
    STYLE_AND_STATE,
)
from jobs.planning import PLAN_INSTRUCTIONS

from .conftest import NO_CHOICES, HeldSteps, facts_ok, paid_for
from .scene_script import script_scene_agents
from .test_producer_broll import (
    checked,  # noqa: F401 (a fixture)
    run,
)
from .test_producer_fixes import shown  # noqa: F401 (a fixture)

pytestmark = [pytest.mark.django_db(transaction=True)]

Say = Callable[..., None]

AS_BROLL: dict[str, Any] = {
    "kind": "broll",
    "kind_why": "This detail needs a close view of the product.",
    "broll_kind": "showcase",
    "person_shown": "no face",
    "usage": None,
    "result": None,
}
SHOWS_1 = "The mug on a sunny windowsill, steam rising from it."


def answer_of(tool: str) -> str:
    """What the newest call of `tool` handed back."""
    call = ToolCall.objects.filter(tool=tool).last()
    assert call is not None, f"{tool} was called"
    return call.result


def owner_says(fake_model: FakeModel, say: Say, text: str, *calls: tuple[str, Any]) -> None:
    """The shop owner sends `text`; the producer makes `calls` in one turn, then replies."""
    fake_model.respond("produce", turn(calls=list(calls)), turn(says="Done."))
    say(text)


def test_the_opening_scene_cannot_be_changed_to_broll_and_nothing_changes(
    fake_model: FakeModel,
    shown: ProducedItem,  # noqa: F811
    steps: HeldSteps,
    say: Say,
) -> None:
    scene = Scene.objects.get(number=1)
    before = (
        scene.kind,
        scene.kind_why,
        scene.line,
        scene.shows,
        scene.broll_kind,
        scene.person_shown,
        scene.fact_checked,
        scene.status,
    )
    paid_before = paid_for()
    owner_says(
        fake_model,
        say,
        "Make the first scene show the mug instead of her.",
        ("edit_scene", {"scene": 1, "shows": SHOWS_1, **AS_BROLL}),
    )
    assert answer_of("edit_scene").startswith("Refused:")
    assert "first scene" in answer_of("edit_scene").lower()
    scene.refresh_from_db()
    assert (
        scene.kind,
        scene.kind_why,
        scene.line,
        scene.shows,
        scene.broll_kind,
        scene.person_shown,
        scene.fact_checked,
        scene.status,
    ) == before
    assert paid_for() == paid_before
    assert steps.held == []


SHOWS_3 = "The mug beside its price tag on a shop shelf."
# Both kinds need at least ten words at the fixed speaking speed.
LINE_1 = "Meet the Stoneware Mug, hand-thrown by Kiln & Co at home every single day."
LINE_2 = "Hand-thrown and holds 350 ml, it looks great on any shop shelf too."


def test_a_broll_scene_given_a_new_action_marks_only_its_clip_stale_and_makes_a_new_ad(
    fake_model: FakeModel,
    shown: ProducedItem,  # noqa: F811
    steps: HeldSteps,
    say: Say,
) -> None:
    owner_says(
        fake_model,
        say,
        "Show the mug on a shop shelf instead.",
        ("edit_scene", {"scene": 2, "line": LINE_2, "shows": SHOWS_3, **AS_BROLL}),
    )
    assert list(Scene.objects.order_by("number").values_list("kind", flat=True)) == [
        "talking",
        "broll",
        "talking",
    ]
    assert "Scene 2's starting picture and clip are stale" in answer_of("edit_scene")
    assert [stale.stale_steps(scene) for scene in Scene.objects.order_by("number")] == [
        [],
        ["starting_picture", "clip"],
        [],
    ]
    paid_before = len(paid_for())

    # The scripted producer has them checked and made again, then assembles.
    fake_model.respond("fact_check", facts_ok(2))
    owner_says(fake_model, say, "Go on", ("run_planning_checks", NO_CHOICES))
    script_scene_agents(fake_model, scenes=1)
    owner_says(
        fake_model,
        say,
        "Go on",
        *[
            (
                "start_scene_agent",
                {
                    "scene": number,
                    "instruction": "Show this scene's planned B-roll action.",
                    "quotes": ["Show the mug on a shop shelf instead"],
                },
            )
            for number in (2,)
        ],
    )
    run(fake_model, steps)
    owner_says(fake_model, say, "Go on", ("assemble_ad", {}))

    # Only the changed scene was made again.
    paid = paid_for()[paid_before:]
    assert (paid.count("speak_line"), paid.count("make_broll_clip")) == (0, 1)
    assert "make_talking_clip" not in paid
    ad = ProducedItem.objects.filter(kind="finished_ad").order_by("version").last()
    assert ad is not None and ad.version == 2
    assert [cut["clip"] for cut in ad.cuts][2] == shown.cuts[2]["clip"]
    # The producer is told what the change cost, for its one line to the shop owner.
    spent = ModelCall.objects.filter(
        created_at__gt=shown.created_at, created_at__lt=ad.created_at
    ).aggregate(cost=Sum("cost_usd"))["cost"]
    assert f"The changes since ad version 1 cost {dollars(spent)}" in answer_of("assemble_ad")
    # The shop owner is shown the new ad.
    assert Attachment.objects.filter(file=ad.file).exists()


# The last line keeps saying the brand (Julian, Nov 8 2026).
SHORT_3 = "Get your Stoneware Mug from Kiln & Co for $24.00 today and enjoy."


def test_a_shorter_line_makes_only_that_scenes_picture_and_clip_stale(
    fake_model: FakeModel,
    shown: ProducedItem,  # noqa: F811
    steps: HeldSteps,
    say: Say,
) -> None:
    owner_says(fake_model, say, "Make it shorter.", ("edit_scene", {"scene": 3, "line": SHORT_3}))

    # A changed line invalidates its picture and clip together.
    assert [stale.stale_steps(scene) for scene in Scene.objects.order_by("number")] == [
        [],
        [],
        ["starting_picture", "clip"],
    ]
    assert "Scene 3's starting picture and clip are stale" in answer_of("edit_scene")
    paid_before = len(paid_for())

    fake_model.respond("fact_check", facts_ok(3))
    owner_says(fake_model, say, "Go on", ("run_planning_checks", NO_CHOICES))
    script_scene_agents(fake_model)
    owner_says(
        fake_model,
        say,
        "Go on",
        (
            "start_scene_agent",
            {"scene": 3, "instruction": "Film the planned action.", "quotes": ["Go on"]},
        ),
    )
    run(fake_model, steps)
    owner_says(fake_model, say, "Go on", ("assemble_ad", {}))

    paid = paid_for()[paid_before:]
    assert paid.count("fact_check") == 1
    assert paid.count("make_broll_clip") == 1
    assert paid.count("transcribe_line") == 1
    assert "speak_line" not in paid
    # The scene agent made a new picture for the new line.
    clip = ProducedItem.objects.filter(kind="clip", scene__number=3).order_by("version").last()
    assert clip is not None and clip.picture is not None and clip.picture.version == 2
    ad = ProducedItem.objects.get(kind="finished_ad", version=2)
    assert ad.cuts[0]["clip"] == shown.cuts[0]["clip"]
    assert ad.cuts[1]["clip"] == shown.cuts[1]["clip"]


def test_an_edit_of_a_scenes_style_keeps_its_other_fields_and_makes_nothing_stale(
    fake_model: FakeModel,
    shown: ProducedItem,  # noqa: F811
    say: Say,
) -> None:
    before = Scene.objects.get(number=3)
    owner_says(
        fake_model,
        say,
        "Film the last scene while she uses it.",
        (
            "edit_scene",
            {"scene": 3, "talking_style": "talking while doing it"},
        ),
    )

    scene = Scene.objects.get(number=3)
    assert scene.talking_style == "talking while doing it"
    assert (scene.product_state, scene.shows_to_lens, scene.packaging_photo, scene.line) == (
        before.product_state,
        before.shows_to_lens,
        before.packaging_photo,
        before.line,
    )
    assert [stale.stale_steps(each) for each in Scene.objects.order_by("number")] == [[], [], []]


def test_make_it_shorter_by_removing_a_scene_pays_for_nothing(
    fake_model: FakeModel,
    shown: ProducedItem,  # noqa: F811
    steps: HeldSteps,
    say: Say,
) -> None:
    # A two-scene ad is its first and last scenes, both talking (Julian, Nov 8 2026).
    removed_clip = ProducedItem.objects.get(kind="clip", scene__number=2).file
    owner_says(fake_model, say, "Make it shorter.", ("remove_scene", {"scene": 2}))

    assert list(Scene.objects.order_by("number").values_list("number", "line")) == [
        (1, "Meet the Stoneware Mug from Kiln & Co for your favourite tea."),
        (2, "Yours for $24.00, get your Stoneware Mug from Kiln & Co today."),
    ]
    answer = answer_of("remove_scene")
    assert "scene 2 removed" in answer
    assert "Nothing made for the ad is stale." in answer
    # The removed scene's files in the chat are on no card.
    shown_clips = Attachment.objects.filter(kind="video", scene__isnull=False)
    assert set(shown_clips.values_list("scene", flat=True)) == {1, 2}
    assert Attachment.objects.get(file=removed_clip).scene is None
    paid_before = len(paid_for())

    owner_says(fake_model, say, "Go on", ("assemble_ad", {}))

    ad = ProducedItem.objects.get(kind="finished_ad", version=2)
    assert [cut["clip"] for cut in ad.cuts] == [shown.cuts[0]["clip"], shown.cuts[2]["clip"]]
    assert paid_for()[paid_before:] == []
    assert "The changes since ad version 1 cost $" in answer_of("assemble_ad")


@pytest.mark.parametrize(
    ("tool", "arguments", "said"),
    [
        pytest.param(
            "remove_scene",
            {"scene": 3},
            f"Scene 2, the last, is B-roll. {LAST_IS_TALKING}",
            id="removing the talking last scene",
        ),
        pytest.param(
            "reorder_scenes",
            {"order": [3, 1, 2]},
            f"Scene 2 is a middle scene talking in another style. {MIDDLE_IS_BROLL}",
            id="moving a talking selfie into the middle",
        ),
        pytest.param(
            "edit_scene",
            {"scene": 1, "product_state": "in its box", "packaging_photo": 40},
            "scene 1 names picture 40 as its packaging photo: the library has no such picture.",
            id="a packaging photo the library doesn't hold",
        ),
        pytest.param(
            "edit_scene",
            {"scene": 2, "kind": "talking", "talking_style": "talking while doing it"},
            f"Scene 2 is a talking scene without a talking style or a product state from the "
            f"lists. {STYLE_AND_STATE}",
            id="a scene turned talking with no product state",
        ),
        pytest.param(
            "edit_scene",
            {"scene": 1, "product_state": "in its box"},
            f'Scene 1 is "in its box" with no packaging photo. {BOX_NEEDS_PACKAGING}',
            id="a box state with no packaging photo",
        ),
    ],
)
def test_a_plan_edit_that_breaks_a_broll_or_talking_rule_is_refused_and_changes_nothing(
    fake_model: FakeModel,
    shown: ProducedItem,  # noqa: F811
    say: Say,
    tool: str,
    arguments: dict[str, Any],
    said: str,
) -> None:
    before = list(
        Scene.objects.order_by("number").values_list(
            "pk", "line", "kind", "talking_style", "product_state"
        )
    )

    owner_says(fake_model, say, "Change the plan.", (tool, arguments))

    answer = " ".join(answer_of(tool).split())
    assert answer.startswith("Refused: ")
    assert said in answer
    assert (
        list(
            Scene.objects.order_by("number").values_list(
                "pk", "line", "kind", "talking_style", "product_state"
            )
        )
        == before
    )


def test_the_last_scene_filmed_in_the_first_ones_style_is_kept_and_shown_to_the_producer(
    fake_model: FakeModel,
    shown: ProducedItem,  # noqa: F811
    say: Say,
) -> None:
    # Told, never refused (Julian, Nov 8 2026): the producer keeps to it by seeing each talking
    # scene's style and state.
    owner_says(
        fake_model,
        say,
        "Film the end as a selfie.",
        ("edit_scene", {"scene": 3, "talking_style": "selfie"}),
    )

    answer = " ".join(answer_of("edit_scene").split())
    assert not answer.startswith("Refused: ")
    assert DIFFERENT_STYLE_AND_STATE not in answer
    assert Scene.objects.get(number=3).talking_style == "selfie"
    first = Scene.objects.get(number=1)
    assert f"1. [talking; style: selfie; product: {first.product_state}] {first.line}" in answer
    assert "3. [talking; style: selfie; product:" in answer
    assert f"2. [B-roll] {Scene.objects.get(number=2).line}" in answer
    # Every whole-plan rule the planner is told goes to the producer in the same words.
    sent = ModelCall.objects.filter(purpose="produce").order_by("id").last()
    assert sent is not None and sent.system_prompt is not None
    told = " ".join(sent.system_prompt.instructions.split())
    for rule in (FIRST_IS_TALKING, *RULES):
        assert " ".join(rule.split()) in told
        assert " ".join(rule.split()) in " ".join(PLAN_INSTRUCTIONS.split())


def test_an_edit_of_one_scene_breaking_a_whole_plan_rule_goes_ahead_with_a_developer_notice(
    fake_model: FakeModel,
    shown: ProducedItem,  # noqa: F811
    say: Say,
) -> None:
    # A change to one scene isn't refused by the whole-plan rules it newly breaks (Julian,
    # Nov 9 2026); the rules it breaks are recorded for the developer.
    owner_says(
        fake_model,
        say,
        "Just show the mug to the camera in the middle.",
        ("edit_scene", {"scene": 2, "shows_to_lens": True}),
    )

    assert not answer_of("edit_scene").startswith("Refused: ")
    assert Scene.objects.get(number=2).shows_to_lens is True
    [notice] = [
        warning["text"]
        for warning in Job.objects.get().warnings
        if warning["kind"] == notices.PLAN_RULE_LET_THROUGH.name
    ]
    assert notice.startswith("The producer's edit_scene changed scene 2 and it went ahead")
    assert f"Scene 2 only shows the product to the lens. {LENS_FIRST_OR_LAST}" in notice


def test_the_last_scene_turned_into_broll_is_refused_and_stays_talking(
    fake_model: FakeModel,
    shown: ProducedItem,  # noqa: F811
    say: Say,
) -> None:
    # The last scene does the after and the casual ask on camera (Julian, Nov 8 2026).
    owner_says(
        fake_model,
        say,
        "Show the mug on the table at the end instead.",
        (
            "edit_scene",
            {
                "scene": 3,
                "kind": "broll",
                "kind_why": "The mug on the table cannot be shown at arm's length.",
                "shows": "A hand sets the mug down on the table",
                "broll_kind": "showcase",
            },
        ),
    )

    answer = " ".join(answer_of("edit_scene").split())
    assert answer.startswith("Refused: the plan would break a rule: Scene 3, the last, is B-roll.")
    assert LAST_IS_TALKING in answer
    assert Scene.objects.get(number=3).kind == Scene.Kind.TALKING


def test_a_scene_turned_into_broll_keeps_no_talking_style_or_product_state() -> None:
    scene = Scene(
        number=2,
        line="A line.",
        kind=Scene.Kind.TALKING,
        talking_style="selfie",
        product_state="in use",
        packaging_photo=2,
    )

    plan_edits._apply(
        scene,
        {
            "kind": "broll",
            "kind_why": "The mug on the table cannot be shown at arm's length.",
            "shows": "A hand sets the mug down on the table",
            "broll_kind": "showcase",
        },
    )

    assert (scene.kind, scene.talking_style, scene.product_state, scene.packaging_photo) == (
        Scene.Kind.BROLL,
        "",
        "",
        None,
    )


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        pytest.param(
            "edit_scene",
            {"scene": 3, "line": "Yours for $24.00, get your Stoneware Mug from us today."},
            id="a new last line",
        ),
        pytest.param("reorder_scenes", {"order": [3, 2, 1]}, id="a scene moved to the end"),
    ],
)
def test_a_change_that_drops_the_brand_from_the_last_line_is_refused(
    fake_model: FakeModel,
    shown: ProducedItem,  # noqa: F811
    say: Say,
    tool: str,
    arguments: dict[str, Any],
) -> None:
    # The casual ask says the brand, at planning and after every later change.
    Scene.objects.filter(number=1).update(
        line="Meet the Stoneware Mug, hand-thrown for your favourite tea every day."
    )
    before = list(Scene.objects.order_by("number").values_list("pk", "line"))

    owner_says(fake_model, say, "Change the end.", (tool, arguments))

    answer = " ".join(answer_of(tool).split())
    assert answer.startswith("Refused: ")
    assert 'The last scene\'s line must say the brand, "Kiln & Co", in its casual ask.' in answer
    assert list(Scene.objects.order_by("number").values_list("pk", "line")) == before


def test_scenes_moved_keep_what_was_made_for_them_and_their_chat_cards(
    fake_model: FakeModel,
    shown: ProducedItem,  # noqa: F811
    say: Say,
) -> None:
    before = {scene.line: scene.pk for scene in Scene.objects.order_by("number")}
    clip_of = {
        scene.pk: ProducedItem.objects.filter(kind="clip", scene=scene).get().file
        for scene in Scene.objects.all()
    }

    owner_says(fake_model, say, "Open on the price.", ("reorder_scenes", {"order": [3, 2, 1]}))

    order = list(Scene.objects.order_by("number").values_list("pk", flat=True))
    assert order == [*before.values()][::-1]
    assert "Nothing made for the ad is stale." in answer_of("reorder_scenes")
    # Each scene's card shows its own clip under its new number.
    for number, pk in enumerate(order, start=1):
        assert Attachment.objects.get(file=clip_of[pk]).scene == number

    owner_says(fake_model, say, "Go on", ("assemble_ad", {}))
    ad = ProducedItem.objects.get(kind="finished_ad", version=2)
    assert [cut["clip"] for cut in ad.cuts] == [shown.cuts[i]["clip"] for i in (2, 1, 0)]


def test_an_order_that_misses_a_scene_is_refused_and_changes_nothing(
    fake_model: FakeModel,
    shown: ProducedItem,  # noqa: F811
    say: Say,
) -> None:
    owner_says(fake_model, say, "Swap the first two.", ("reorder_scenes", {"order": [2, 1]}))

    assert answer_of("reorder_scenes").startswith("Refused: the new order names each scene once")
    assert list(Scene.objects.order_by("number").values_list("number", flat=True)) == [1, 2, 3]


def test_an_added_scene_is_checked_then_made_and_the_others_move_later(
    fake_model: FakeModel,
    shown: ProducedItem,  # noqa: F811
    steps: HeldSteps,
    say: Say,
) -> None:
    line = "It keeps your tea hot for a whole hour at home, every day."
    owner_says(
        fake_model,
        say,
        "Add a scene about keeping tea hot.",
        (
            "add_scene",
            {
                "at": 2,
                "line": line,
                "kind": "talking",
                "shows": "The presenter lifts the steaming mug.",
                "talking_style": "talking while doing it",
                "product_state": "beside its box",
                "packaging_photo": 1,
            },
        ),
    )

    assert list(Scene.objects.order_by("number").values_list("number", "line")) == [
        (1, "Meet the Stoneware Mug from Kiln & Co for your favourite tea."),
        (2, line),
        (3, "Hand-thrown, holds 350 ml, and dishwasher safe too. At home, every day."),
        (4, "Yours for $24.00, get your Stoneware Mug from Kiln & Co today."),
    ]
    added = Scene.objects.get(number=2)
    assert (added.fact_checked, added.status) == (False, "planned")
    answer = answer_of("add_scene")
    assert "scene 2 added" in answer
    assert "The planning checks haven't checked scene 2 against the page" in answer
    start = (
        "start_scene_agent",
        {"scene": 2, "instruction": "Film its planned action.", "quotes": ["Go on"]},
    )
    owner_says(fake_model, say, "Go on", start)
    assert answer_of("start_scene_agent").startswith("Refused:")
    assert not steps.held

    fake_model.respond("fact_check", facts_ok(2))
    owner_says(fake_model, say, "Go on", ("run_planning_checks", NO_CHOICES))
    script_scene_agents(fake_model)
    owner_says(fake_model, say, "Go on", start)
    run(fake_model, steps)
    owner_says(fake_model, say, "Go on", ("assemble_ad", {}))

    ad = ProducedItem.objects.get(kind="finished_ad", version=2)
    assert [cut["scene"] for cut in ad.cuts] == [1, 2, 3, 4]
    assert [ad.cuts[i]["clip"] for i in (0, 2, 3)] == [cut["clip"] for cut in shown.cuts]


def test_a_scene_that_would_break_the_plan_is_refused_with_why_and_nothing_changes(
    fake_model: FakeModel,
    shown: ProducedItem,  # noqa: F811
    say: Say,
) -> None:
    # A B-roll scene needs a reason it cannot be shown at arm's length.
    before = Scene.objects.get(number=3)
    owner_says(
        fake_model,
        say,
        "Show the mug in scene 3.",
        ("edit_scene", {"scene": 3, "kind": "broll", "kind_why": "", "shows": SHOWS_1}),
    )

    answer = answer_of("edit_scene")
    assert answer.startswith("Refused: scene 3 wouldn't be a scene the plan can hold")
    assert "B-roll" in answer
    scene = Scene.objects.get(number=3)
    assert (scene.kind, scene.kind_why, scene.shows, scene.fact_checked, scene.status) == (
        before.kind,
        before.kind_why,
        before.shows,
        before.fact_checked,
        before.status,
    )


def test_an_edit_taken_back_hands_back_what_was_made_and_pays_for_nothing(
    fake_model: FakeModel,
    shown: ProducedItem,  # noqa: F811
    say: Say,
) -> None:
    line = Scene.objects.get(number=3).line
    owner_says(fake_model, say, "Make it shorter.", ("edit_scene", {"scene": 3, "line": SHORT_3}))
    paid_before = len(paid_for())

    fake_model.respond("fact_check", facts_ok(3))
    owner_says(
        fake_model,
        say,
        "No, keep it as it was.",
        ("edit_scene", {"scene": 3, "line": line}),
        ("run_planning_checks", NO_CHOICES),
        ("assemble_ad", {}),
    )

    assert "Nothing made for the ad is stale." in answer_of("edit_scene")
    assert stale.stale_steps(Scene.objects.get(number=3)) == []
    # The ad as it was is shown again, and nothing was made again: only its line was checked.
    assert answer_of("assemble_ad").startswith("The ad was already assembled")
    assert paid_for()[paid_before:] == ["fact_check"]


def test_an_edit_while_the_scene_agent_films_is_refused_and_the_clip_finishes_current(
    fake_model: FakeModel,
    shown: ProducedItem,  # noqa: F811
    steps: HeldSteps,
    say: Say,
) -> None:
    owner_says(fake_model, say, "Make it shorter.", ("edit_scene", {"scene": 3, "line": SHORT_3}))
    fake_model.respond("fact_check", facts_ok(3))
    owner_says(fake_model, say, "Go on", ("run_planning_checks", NO_CHOICES))
    script_scene_agents(fake_model)
    owner_says(
        fake_model,
        say,
        "Go on",
        (
            "start_scene_agent",
            {"scene": 3, "instruction": "Film the current line.", "quotes": ["Go on"]},
        ),
    )
    assert len(steps.held) == 1
    owner_says(
        fake_model,
        say,
        "Say it's on sale.",
        (
            "edit_scene",
            {
                "scene": 3,
                "line": (
                    "On sale now, get your favourite Stoneware Mug from our shop every single day."
                ),
            },
        ),
    )
    assert answer_of("edit_scene").startswith("Refused:")
    assert Scene.objects.get(number=3).line == SHORT_3
    run(fake_model, steps)
    scene = Scene.objects.get(number=3)
    assert scene.status == "finished"
    assert stale.stale_steps(scene) == []


def test_an_edit_while_the_fixing_agent_films_the_scene_again_is_refused_and_changes_nothing(
    fake_model: FakeModel,
    shown: ProducedItem,  # noqa: F811
    say: Say,
) -> None:
    scene = Scene.objects.get(number=2)
    made = scene.steps.filter(kind=SceneStep.Kind.CLIP).last()
    assert made is not None
    step = SceneStep.objects.create(
        scene=scene,
        kind=SceneStep.Kind.CLIP,
        status=SceneStep.Status.RUNNING,
        tool_call=made.tool_call,
    )
    AgentFix.objects.create(step=step, door=AgentFix.Door.JUDGE)
    line = scene.line
    owner_says(
        fake_model,
        say,
        "Make scene 2 say it's microwave safe.",
        ("edit_scene", {"scene": 2, "line": "Hand-thrown, and microwave safe too."}),
    )

    answer = answer_of("edit_scene")
    assert answer.startswith("Refused: the fixing agent is filming scene 2 again")
    assert Scene.objects.get(number=2).line == line


def test_new_music_once_the_ad_is_shown_is_put_in_when_the_ad_is_assembled_again(
    fake_model: FakeModel,
    shown: ProducedItem,  # noqa: F811
    say: Say,
) -> None:
    # Nothing locks once the ad has clips (spec #247 point 5; ticket #259): the music is made
    # again at any step, and the producer assembles the ad with it.
    job = shown.job
    first = job.produced.filter(kind="music").latest("version")
    owner_says(
        fake_model,
        say,
        "Calmer music, please.",
        ("create_music", {"mood": "calm piano"}),
        ("assemble_ad", {}),
    )

    music = job.produced.filter(kind="music").latest("version")
    assert (music.version, music.text.startswith("Calm piano.")) == (first.version + 1, True)
    assert answer_of("create_music").startswith(f"Made the music (version {music.version}")
    ad = job.produced.filter(kind="finished_ad").latest("version")
    assert (ad.version, ad.made_from) == (shown.version + 1, music)
    assert f"The changes since ad version {shown.version} cost $" in answer_of("assemble_ad")


def test_the_cost_line_counts_from_the_ad_just_before_not_the_first(
    fake_model: FakeModel,
    shown: ProducedItem,  # noqa: F811
    say: Say,
) -> None:
    for mood in ("calm piano", "soft strings"):
        owner_says(
            fake_model,
            say,
            "Other music, please.",
            ("create_music", {"mood": mood}),
            ("assemble_ad", {}),
        )

    second, third = shown.job.produced.filter(kind="finished_ad").order_by("version")[1:]
    spent = ModelCall.objects.filter(
        created_at__gt=second.created_at, created_at__lt=third.created_at
    ).aggregate(cost=Sum("cost_usd"))["cost"]
    first, *_, last = ToolCall.objects.filter(tool="assemble_ad").order_by("id")
    assert f"The changes since ad version {second.version} cost {dollars(spent)}:" in last.result
    # The first ad has no ad before it to count from.
    assert "The changes since" not in first.result


@pytest.mark.parametrize("edited", [False, True], ids=["clip current", "clip stale"])
def test_a_dead_step_offers_to_keep_the_scenes_clip_only_while_it_is_current(
    fake_model: FakeModel,
    shown: ProducedItem,  # noqa: F811
    steps: HeldSteps,
    say: Say,
    edited: bool,
) -> None:
    if edited:
        owner_says(fake_model, say, "Shorter.", ("edit_scene", {"scene": 3, "line": SHORT_3}))
        fake_model.respond("fact_check", facts_ok(3))
        owner_says(fake_model, say, "Go on", ("run_planning_checks", NO_CHOICES))
    owner_says(
        fake_model,
        say,
        "Make it brighter.",
        (
            "start_scene_agent",
            {"scene": 3, "instruction": "A brighter workshop.", "quotes": ["Make it brighter"]},
        ),
    )
    (step_id,) = steps.held
    steps.held.clear()
    long_ago = timezone.now() - timedelta(minutes=6)
    SceneStep.objects.filter(pk=step_id).update(taken_at=long_ago, seen_at=long_ago)

    fake_model.respond("produce", turn(says="Scene 3's picture stopped."))
    tasks.fail_dead_steps()

    result = SceneStep.objects.get(pk=step_id).result
    keeps = "or skip it: scene 3 keeps the clip it has."
    removes = "or skip it: remove scene 3 with remove_scene, and the ad is made without it."
    assert (keeps in result, removes in result) == (not edited, edited)


@pytest.mark.parametrize("changed_scene", [2, 3])
def test_a_talking_kind_edit_ends_the_broll_second_state_pair(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    say: Say,
    changed_scene: int,
) -> None:
    # A planned pair has scene 2's first state followed by scene 3's second state.
    Scene.objects.filter(number=3).update(
        kind=Scene.Kind.BROLL,
        kind_why="The mug's inside needs a close view away from her face.",
        broll_kind="showcase",
        person_shown=Scene.PersonShown.NO_FACE,
        second_state=True,
    )

    owner_says(
        fake_model,
        say,
        "Have her show it while speaking.",
        (
            "edit_scene",
            {
                "scene": changed_scene,
                "kind": "talking",
                # A middle scene talks only while she uses the mug (ticket #403).
                **(
                    {"talking_style": "talking while doing it", "product_state": "in use"}
                    if changed_scene == 2
                    else {}
                ),
            },
        ),
    )

    assert not answer_of("edit_scene").startswith("Refused:")
    assert Scene.objects.get(number=changed_scene).kind == Scene.Kind.TALKING
    assert not Scene.objects.get(number=3).second_state
