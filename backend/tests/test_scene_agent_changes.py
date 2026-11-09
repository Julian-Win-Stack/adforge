"""Scene row changes through the chat, with outside models scripted."""

from collections.abc import Callable
from typing import Any

import pytest
from rest_framework.test import APIClient

from agents.scene_agent.draw_starting_picture import PORTRAIT_JOB, ROOM_JOB
from gateway.fake import FakeModel, turn
from jobs import notices
from jobs.models import Job, ProducedItem, Scene
from jobs.plan_rules import (
    AT_LEAST_ONE_BROLL,
    BOX_NEEDS_PACKAGING,
    LAST_IS_TALKING,
    LENS_FIRST_OR_LAST,
    SETUP_LOOKS,
)
from jobs.scene_pictures import matches
from jobs.work import latest, scene_place

from .conftest import HeldSteps, facts_ok, handoffs, paid_for, recorded
from .test_producer_broll import BROLL_PLAN, calling, checked, run  # noqa: F401
from .test_scene_agent import scene_agent_ready  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)
LINE_2 = BROLL_PLAN["plan"]["scenes"][1]["line"]
ACTION = "Tea is stirred in the mug with a spoon."


def change(**fields: Any) -> tuple[str, dict[str, Any]]:
    return "change_scene", {
        "line": LINE_2,
        "shows": ACTION,
        "usage": "",
        "result": "",
        "broll_kind": "showcase",
        "person_shown": "no face",
        **fields,
    }


def test_changed_action_shows_the_kept_line_once_then_changes_without_filming(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    fake_model.respond("fact_check", facts_ok(2))
    fake_model.respond(
        "scene_agent",
        turn(calls=[change()]),
        turn(calls=[change()]),
        turn(calls=[("give_up", {"reason": "Need another product view."})]),
    )
    calling(
        fake_model,
        say,
        (
            "start_scene_agent",
            {"scene": 2, "instruction": "Change the action.", "quotes": ["go on"]},
        ),
    )
    run(fake_model, steps)
    requests = handoffs("scene_agent")
    assert f'The scene\'s line says: "{LINE_2}"' in requests[1]["conversation"][-1]["result"]
    assert "Nothing was changed" in requests[1]["conversation"][-1]["result"]
    assert "The scene row changed" in str(requests[2])
    assert handoffs("fact_check")[-1]["lines"][0]["shows"] == ACTION


def test_a_give_up_that_keeps_its_filmed_try_keeps_the_action_it_was_filmed_with(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    from .test_producer_auto_fix import FLOATS, failing
    from .test_scene_film import FILM
    from .test_scene_pictures import PICTURE_FORM, PICTURE_PASSES

    fake_model.answer_unscripted("check_scene_picture", lambda _: PICTURE_PASSES)
    fake_model.respond("fact_check", facts_ok(2))
    fake_model.respond("check_clip_product", failing(FLOATS))
    fake_model.respond(
        "scene_agent",
        turn(calls=[change()]),
        turn(calls=[change()]),
        turn(calls=[("draw_starting_picture", PICTURE_FORM)]),
        turn(calls=[("film", FILM)]),
        turn(calls=[("give_up", {"reason": "Need another product view."})]),
    )
    calling(
        fake_model,
        say,
        (
            "start_scene_agent",
            {"scene": 2, "instruction": "Change the action.", "quotes": ["go on"]},
        ),
    )
    run(fake_model, steps)

    assert paid_for().count("make_broll_clip") == 1
    # The try stays in the ad, so the scene is as that try was filmed.
    scene = Scene.objects.get(number=2)
    assert scene.shows == ACTION


def test_a_new_line_is_checked_without_voice_spending_then_giveup_restores_the_plan(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    fake_model.respond("produce", turn(says="I will pass on that note."))
    say("Keep the handle visible.")
    line = "This mug holds three hundred and fifty millilitres of your favourite tea."
    fake_model.respond("fact_check", facts_ok(2))
    fake_model.respond(
        "scene_agent",
        turn(calls=[change(line=line)]),
        turn(calls=[("give_up", {"reason": "Need a different product view."})]),
    )
    calling(
        fake_model,
        say,
        (
            "start_scene_agent",
            {
                "scene": 2,
                "instruction": "Change the scene.",
                "quotes": ["Keep the handle visible."],
            },
        ),
    )
    run(fake_model, steps)
    result = handoffs("scene_agent")[1]["conversation"][-1]["result"]
    assert "The scene row changed" in result
    assert "Nothing was drawn or filmed" in result
    assert not {"design_voice", "measure_voice", "speak_line", "transcribe_line"} & set(paid_for())
    assert handoffs("fact_check")[-1]["lines"][0]["line"] == line
    assert handoffs("fact_check")[-1]["conversation"] == [
        {"by": "user", "text": "Keep the handle visible."}
    ]
    # The producer sees the restored original row on its next turn.
    snapshot = handoffs("produce")[-1]["snapshot"]
    assert LINE_2 in snapshot
    assert line not in snapshot
    assert ACTION not in snapshot


def test_a_fact_check_failure_refuses_the_change_with_the_reason(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    line = "This mug keeps your favourite tea hot for many hours every day."
    fake_model.respond(
        "fact_check",
        {
            "decision": "checked",
            "reason": "Claim not on page.",
            "question": None,
            "lines": [
                {
                    "scene": 2,
                    "verdict": "wrong",
                    "wrong": "line",
                    "problem": "Heat retention is not stated.",
                    "page_says": "It holds 350 ml.",
                }
            ],
        },
    )
    fake_model.respond(
        "scene_agent",
        turn(calls=[change(line=line)]),
        turn(calls=[("give_up", {"reason": "Need another view."})]),
    )
    calling(
        fake_model,
        say,
        (
            "start_scene_agent",
            {"scene": 2, "instruction": "Change the scene.", "quotes": ["go on"]},
        ),
    )
    run(fake_model, steps)
    result = handoffs("scene_agent")[1]["conversation"][-1]["result"]
    assert "Refused: the fact check failed it: Heat retention is not stated." in result
    assert "make_broll_clip" not in paid_for()


def test_a_line_over_fourteen_seconds_is_refused_before_filming(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    fake_model.respond("fact_check", facts_ok(2))
    fake_model.respond(
        "scene_agent",
        turn(calls=[change(line="This mug holds tea. " * 10)]),
        turn(calls=[("give_up", {"reason": "Need a shorter line."})]),
    )
    calling(
        fake_model,
        say,
        (
            "start_scene_agent",
            {"scene": 2, "instruction": "Change the scene.", "quotes": ["go on"]},
        ),
    )
    run(fake_model, steps)
    result = handoffs("scene_agent")[1]["conversation"][-1]["result"]
    assert "more than 14 seconds" in result


def test_an_action_another_scene_films_is_refused_without_another_check(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    calling(
        fake_model,
        say,
        (
            "edit_scene",
            {
                "scene": 3,
                "shows": ACTION,
                "broll_kind": "showcase",
                "person_shown": "no face",
                "usage": "",
                "result": "",
            },
        ),
    )
    checks = len(handoffs("fact_check"))
    fake_model.respond(
        "scene_agent",
        turn(calls=[change()]),
        turn(calls=[("give_up", {"reason": "That action belongs to another scene."})]),
    )
    calling(
        fake_model,
        say,
        (
            "start_scene_agent",
            {"scene": 2, "instruction": "Change the action.", "quotes": ["go on"]},
        ),
    )
    run(fake_model, steps)
    result = handoffs("scene_agent")[1]["conversation"][-1]["result"]
    assert "Refused: scene 3 already films this action." in result
    assert len(handoffs("fact_check")) == checks


@pytest.mark.parametrize("changed_scene", [2, 3])
def test_a_kind_change_keeps_second_state_pairs_valid_and_refusal_changes_nothing(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    changed_scene: int,
) -> None:
    from jobs.models import Scene

    from .test_scene_film import FILM
    from .test_scene_pictures import PICTURE_FORM, PICTURE_PASSES

    # The planned B-roll pair ends in scene 3. Either scene can later become talking.
    Scene.objects.filter(number=3).update(
        kind=Scene.Kind.BROLL,
        kind_why="The mug's inside needs a close view away from her face.",
        broll_kind="showcase",
        person_shown=Scene.PersonShown.NO_FACE,
        second_state=True,
    )
    scene = Scene.objects.get(number=changed_scene)
    fields = {
        name: getattr(scene, name)
        for name in ("line", "shows", "usage", "result", "broll_kind", "person_shown")
    }
    wanted = {**fields, "kind": "talking"}
    if changed_scene == 2:
        # A middle scene talks only while she uses the mug (ticket #403).
        wanted.update(talking_style="talking while doing it", product_state="in use")
    fake_model.respond("fact_check", facts_ok(changed_scene))
    fake_model.respond("transcribe_line", {"text": scene.line})
    fake_model.answer_unscripted("check_scene_picture", lambda _: PICTURE_PASSES)

    def after_refusal() -> Any:
        scene.refresh_from_db()
        assert (scene.kind, scene.line) == (Scene.Kind.BROLL, fields["line"])
        assert Scene.objects.get(number=3).second_state
        return turn(calls=[("change_scene", wanted)])

    def after_change() -> Any:
        scene.refresh_from_db()
        assert scene.kind == Scene.Kind.TALKING
        assert not Scene.objects.get(number=3).second_state
        return turn(calls=[("draw_starting_picture", PICTURE_FORM)])

    fake_model.respond(
        "scene_agent",
        turn(calls=[("change_scene", {**wanted, "line": "mug " * 40})]),
        after_refusal,
        after_change,
        turn(calls=[("film", FILM)]),
    )
    calling(
        fake_model,
        say,
        (
            "start_scene_agent",
            {"scene": changed_scene, "instruction": "Show it while speaking.", "quotes": ["Go on"]},
        ),
    )
    run(fake_model, steps)

    scene.refresh_from_db()
    assert scene.kind == Scene.Kind.TALKING
    assert scene.status == Scene.Status.FINISHED
    assert not Scene.objects.get(number=3).second_state


def test_a_talking_scene_is_told_its_style_and_state_and_its_picture_is_drawn_in_them(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    from .test_scene_pictures import PICTURE_FORM, PICTURE_PASSES

    # Ticket #403: the opening shows the mug beside its box, from the packaging photo.
    Scene.objects.filter(number=1).update(product_state="beside its box", packaging_photo=2)
    fake_model.answer_unscripted("check_scene_picture", lambda _: PICTURE_PASSES)
    fake_model.respond(
        "scene_agent",
        turn(calls=[("draw_starting_picture", PICTURE_FORM)]),
        turn(calls=[("give_up", {"reason": "Another view is needed."})]),
    )
    calling(
        fake_model,
        say,
        ("start_scene_agent", {"scene": 1, "instruction": "Open on it.", "quotes": ["go on"]}),
    )
    run(fake_model, steps)

    facts = " ".join(handoffs("scene_agent")[0]["conversation"][0]["text"].split())
    assert (
        f"talking style: selfie ({SETUP_LOOKS['selfie']}) product state: beside its box (the "
        "product out, its box beside it as the packaging photo shows it) packaging photo: "
        "library item 2" in facts
    )
    assert f"talking style: propped ({SETUP_LOOKS['propped']})" in facts
    # The camera setup is said first, then the photos: her face from the portrait and the room
    # alone, never the shared picture's camera (Julian, Nov 9 2026).
    prompt = " ".join(handoffs("draw_scene_picture")[0]["prompt"].split())
    assert prompt.startswith(f"How she is filmed: {SETUP_LOOKS['selfie']}.")
    assert "Where the product is: the product out, its box beside it" in prompt
    assert "Image 2: the product's packaging; draw its box as this photo shows it." in prompt
    assert f"Image 3: {PORTRAIT_JOB}" in prompt
    assert f"Image 4: {ROOM_JOB}" in prompt
    made = ProducedItem.objects.get(kind="starting_picture", scene__number=1)
    assert made.sent is not None
    photos = made.sent["photos"]
    assert [photo.get("library_number") for photo in photos][:2] == [1, 2]
    portrait = latest(made.job, ProducedItem.Kind.PORTRAIT)
    room = latest(made.job, ProducedItem.Kind.ROOM_ALONE)
    assert portrait is not None and room is not None
    assert [photo["file"] for photo in photos][2:] == [portrait.file, room.file]
    # Drawn from the portrait and the room, the picture is current and can be filmed; so is
    # one drawn before from the shared picture of her in the room.
    scene = Scene.objects.get(number=1)
    assert matches(made, scene)
    _, shared = scene_place(scene, scene.person_shown)
    assert shared is not None
    made.sent["photos"] = [*photos[:2], {"file": shared.file, "line": "Image 3 shows ..."}]
    assert matches(made, scene)
    made.sent["photos"] = [*photos[:2], {"file": room.file, "line": "Image 3 shows ..."}]
    assert not matches(made, scene)


def test_her_talking_picture_leaves_room_for_her_portrait_and_the_room_in_its_photos(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    from .test_scene_pictures import PICTURE_FORM

    # The picture model takes 16 pictures; her talking adds her portrait and the room alone.
    fake_model.respond(
        "scene_agent",
        turn(
            calls=[
                (
                    "draw_starting_picture",
                    {**PICTURE_FORM, "picks": [{"item": 1, "take": "The mug."}] * 15},
                )
            ]
        ),
        turn(calls=[("give_up", {"reason": "Another view is needed."})]),
    )
    calling(
        fake_model,
        say,
        ("start_scene_agent", {"scene": 1, "instruction": "Open on it.", "quotes": ["go on"]}),
    )
    run(fake_model, steps)

    refused = " ".join(handoffs("scene_agent")[1]["conversation"][-1]["result"].split())
    assert refused.startswith("Refused: 14 at most for her talking")
    assert not handoffs("draw_scene_picture")


def test_a_change_to_b_roll_is_refused_and_the_last_scene_stays_talking(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    last = Scene.objects.get(number=3)
    to_the_table = "A hand sets the mug down on the table."
    as_broll = change(
        line=last.line,
        shows=to_the_table,
        kind="broll",
        kind_why="The mug on the table cannot be shown at arm's length.",
    )

    def kept_talking() -> Any:
        scene = Scene.objects.get(number=3)
        assert (scene.kind, scene.talking_style) == ("talking", last.talking_style)
        return turn(calls=[("give_up", {"reason": "Another view is needed."})])

    fake_model.respond(
        "scene_agent",
        turn(calls=[as_broll]),
        turn(calls=[as_broll]),
        kept_talking,
    )
    calling(
        fake_model,
        say,
        ("start_scene_agent", {"scene": 3, "instruction": "Change it.", "quotes": ["go on"]}),
    )
    run(fake_model, steps)

    # The last scene stays talking: it does the after and the ask (Julian, Nov 8 2026).
    as_broll_refused = " ".join(handoffs("scene_agent")[2]["conversation"][-1]["result"].split())
    assert as_broll_refused.startswith("Refused: the plan would break a rule: Scene 3, the last")
    assert LAST_IS_TALKING in as_broll_refused


def test_a_last_line_that_drops_the_brand_is_refused_and_nothing_changes(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    # The casual ask says the brand after every later change (Julian, Nov 8 2026).
    last = Scene.objects.get(number=3)
    assert last.job.brand and last.job.brand in last.line
    fake_model.respond(
        "scene_agent",
        turn(
            calls=[
                change(
                    line="Honestly, my tea stays warm all morning. It's $24.00, just get one.",
                    shows=last.shows,
                )
            ]
        ),
        turn(calls=[("give_up", {"reason": "Another view is needed."})]),
    )
    calling(
        fake_model,
        say,
        ("start_scene_agent", {"scene": 3, "instruction": "Change it.", "quotes": ["go on"]}),
    )
    run(fake_model, steps)

    refused = " ".join(handoffs("scene_agent")[1]["conversation"][-1]["result"].split())
    assert refused == (
        f'Refused: The last scene\'s line must say the brand, "{last.job.brand}", in its casual '
        "ask. Nothing was done."
    )
    assert Scene.objects.get(number=3).line == last.line


def test_an_excluded_packaging_photo_is_left_out_of_the_picture_with_a_notice(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    api: APIClient,
    session_id: str,
) -> None:
    from .test_scene_pictures import PICTURE_FORM, PICTURE_PASSES

    Scene.objects.filter(number=1).update(
        product_state="beside its box", packaging_photo=2, excluded=[2]
    )
    fake_model.answer_unscripted("check_scene_picture", lambda _: PICTURE_PASSES)
    fake_model.respond(
        "scene_agent",
        turn(calls=[("draw_starting_picture", PICTURE_FORM)]),
        turn(calls=[("give_up", {"reason": "Another view is needed."})]),
    )
    calling(
        fake_model,
        say,
        ("start_scene_agent", {"scene": 1, "instruction": "Open on it.", "quotes": ["go on"]}),
    )
    run(fake_model, steps)

    made = ProducedItem.objects.get(kind="starting_picture", scene__number=1)
    assert made.sent is not None
    assert [photo.get("library_number") for photo in made.sent["photos"]][:1] == [1]
    assert all(photo.get("library_number") != 2 for photo in made.sent["photos"])
    assert (
        "problem",
        "Scene 1's packaging photo, picture 2, was left out of its starting picture "
        '("beside its box"): the owner excluded it.',
    ) in recorded(api, session_id)


def test_a_box_state_naming_no_packaging_photo_is_drawn_with_a_notice(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    api: APIClient,
    session_id: str,
) -> None:
    # The box rule is only told since Nov 9 2026 (rules review), so a kept plan may name a box
    # state with no packaging photo.
    from .test_scene_pictures import PICTURE_FORM, PICTURE_PASSES

    Scene.objects.filter(number=1).update(product_state="in its box", packaging_photo=None)
    fake_model.answer_unscripted("check_scene_picture", lambda _: PICTURE_PASSES)
    fake_model.respond(
        "scene_agent",
        turn(calls=[("draw_starting_picture", PICTURE_FORM)]),
        turn(calls=[("give_up", {"reason": "Another view is needed."})]),
    )
    calling(
        fake_model,
        say,
        ("start_scene_agent", {"scene": 1, "instruction": "Open on it.", "quotes": ["go on"]}),
    )
    run(fake_model, steps)

    assert ProducedItem.objects.filter(kind="starting_picture", scene__number=1).exists()
    assert (
        "problem",
        'Scene 1 is "in its box" but names no packaging photo, so its starting picture draws '
        "a box no photo shows.",
    ) in recorded(api, session_id)


def test_a_broll_give_up_offers_a_talking_scene_only_where_the_rules_allow_one(
    scene_agent_ready: None,  # noqa: F811
) -> None:
    from jobs.work import scene_way_out

    middle = Scene.objects.get(number=2)
    # The ad's only B-roll scene stays B-roll.
    assert scene_way_out(middle) == "You can send a photo."
    Scene.objects.filter(number=3).update(kind=Scene.Kind.BROLL)
    # A middle scene talks only while she uses it; the last may just hold it.
    assert scene_way_out(middle) == (
        "We can make it a scene where she uses it and talks about it, or you can send a photo."
    )
    assert scene_way_out(Scene.objects.get(number=3)) == (
        "We can make it a scene where she holds it and talks about it, or you can send a photo."
    )


def started(fake_model: FakeModel, say: Callable[..., None], scene: int) -> None:
    calling(
        fake_model,
        say,
        ("start_scene_agent", {"scene": scene, "instruction": "Change it.", "quotes": ["go on"]}),
    )


def result_of(turn_number: int) -> str:
    """What the scene agent's tool answered just before its `turn_number`th turn (from 0)."""
    return " ".join(handoffs("scene_agent")[turn_number]["conversation"][-1]["result"].split())


GIVE_UP = turn(calls=[("give_up", {"reason": "Another view is needed."})])
# A middle scene's new line, said while she holds the mug up to the lens.
TO_THE_LENS = change(
    line="Look how the glaze on this mug catches the morning light every single time.",
    shows="She holds the mug up to the lens.",
    kind="talking",
    talking_style="talking while doing it",
    product_state="beside its box",
    packaging_photo=2,
    shows_to_lens=True,
)


def test_a_middle_scene_falling_back_to_the_lens_goes_ahead_with_a_developer_notice(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    # Round 12's KONG scene 3 was lost when this fallback was refused (Julian, Nov 9 2026): a
    # change to one scene goes ahead, and the rules it now breaks are recorded.
    fake_model.respond("fact_check", facts_ok(2))

    def changed() -> Any:
        scene = Scene.objects.get(number=2)
        assert (scene.kind, scene.shows_to_lens) == ("talking", True)
        return GIVE_UP

    fake_model.respond("scene_agent", turn(calls=[TO_THE_LENS]), changed)
    started(fake_model, say, 2)
    run(fake_model, steps)

    assert result_of(1).startswith("The scene row changed:")
    [notice] = [
        warning["text"]
        for warning in Job.objects.get().warnings
        if warning["kind"] == notices.PLAN_RULE_LET_THROUGH.name
    ]
    assert notice.startswith("The scene agent changed scene 2 and it went ahead")
    assert f"Scene 2 only shows the product to the lens. {LENS_FIRST_OR_LAST}" in notice
    # Scene 2 was the ad's only B-roll scene, so that rule is named too.
    assert AT_LEAST_ONE_BROLL in notice
    # The agent is told it may: the lens is its last fallback in any scene (Julian, Nov 9).
    from agents.scene_agent.instructions import INSTRUCTIONS

    told = " ".join(INSTRUCTIONS.split())
    assert "show the product to the lens while she says the line, in any scene, a middle" in told
    assert "lets any other rule a change breaks through" in told


@pytest.mark.parametrize(
    ("changed", "refused"),
    [
        pytest.param(
            {"talking_style": "walking to camera"},
            "it was called with arguments it can't use (talking_style:",
            id="a style not from the list",
        ),
        pytest.param(
            {"product_state": "in its box"},
            f'Scene 3 is "in its box" with no packaging photo. {BOX_NEEDS_PACKAGING}',
            id="a box state with no packaging photo",
        ),
    ],
)
def test_a_changed_talking_scene_breaking_a_talking_rule_is_refused(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    changed: dict[str, Any],
    refused: str,
) -> None:
    last = Scene.objects.get(number=3)
    fake_model.respond(
        "scene_agent",
        turn(calls=[change(line=last.line, shows=last.shows, **changed)]),
        GIVE_UP,
    )
    started(fake_model, say, 3)
    run(fake_model, steps)

    assert refused in result_of(1)
    assert result_of(1).startswith("Refused:")
    after = Scene.objects.get(number=3)
    assert (after.talking_style, after.product_state) == (
        last.talking_style,
        last.product_state,
    )


@pytest.mark.parametrize(
    "changed",
    [
        pytest.param({"product_state": "out of the box"}, id="the previous talking scene's state"),
        pytest.param({"talking_style": "selfie"}, id="the previous talking scene's style"),
    ],
)
def test_a_talking_scene_changed_to_the_previous_ones_style_or_state_is_kept(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    changed: dict[str, Any],
) -> None:
    # Told, never refused (Julian, Nov 8 2026): scene 1 is a selfie, out of the box.
    last = Scene.objects.get(number=3)
    fake_model.respond(
        "scene_agent",
        turn(calls=[change(line=last.line, shows=last.shows, **changed)]),
        GIVE_UP,
    )
    started(fake_model, say, 3)
    run(fake_model, steps)

    assert result_of(1).startswith("The scene row changed:")
    [name] = changed
    assert name in result_of(1)


def test_a_new_action_with_the_lens_flag_left_out_is_not_a_lens_shot(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    # The opening only showed the mug to the lens.
    Scene.objects.filter(number=1).update(shows_to_lens=True)
    opening = Scene.objects.get(number=1)
    sipped = change(line=opening.line, shows="She sips tea from the mug and smiles.")
    fake_model.respond("fact_check", facts_ok(1))

    def changed() -> Any:
        assert Scene.objects.get(number=1).shows_to_lens is False
        return GIVE_UP

    fake_model.respond("scene_agent", turn(calls=[sipped]), turn(calls=[sipped]), changed)
    started(fake_model, say, 1)
    run(fake_model, steps)

    assert result_of(2).startswith("The scene row changed: shows")
    assert "shows_to_lens" in result_of(2)
