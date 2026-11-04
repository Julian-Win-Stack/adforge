"""Only consecutive product faults ask the scene agent for a bigger change."""

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from gateway.fake import FakeModel

    from .conftest import ScriptedProducer


@pytest.mark.parametrize(
    ("verdicts", "count"),
    [
        ([], 0),
        (["fail"], 1),
        (["fail", "fail"], 2),
        (["fail", "fail", "fail"], 3),
        (["fail", "fail", "pass"], 0),
        (["fail", "fail", "pass", "fail"], 1),
        (["fail", "fail", "unsure", "fail"], 1),
        (["fail", "fail", None, "fail"], 1),
    ],
)
def test_only_the_last_unbroken_product_fails_count(verdicts: list[str | None], count: int) -> None:
    from agents.scene_agent.lifecycle import consecutive_product_failures

    assert consecutive_product_failures(verdicts) == count


@pytest.mark.django_db(transaction=True)
def test_two_product_fails_add_guidance_and_word_only_failure_resets_it(
    producer: ScriptedProducer,
    fake_model: FakeModel,
    product_page_url: str,
) -> None:
    from gateway.fake import turn

    from .conftest import CLIP_PRODUCT_OK, NO_CHOICES, READABLE, facts_ok, handoffs, results_of
    from .test_h3_planning import h3_plan
    from .test_producer_auto_fix import failing
    from .test_scene_film import FILM
    from .test_scene_pictures import PICTURE_FORM, PICTURE_PASSES

    planned = h3_plan()
    line = planned["plan"]["scenes"][0]["line"]
    fake_model.respond("check_page", READABLE)
    fake_model.respond("plan_ad", planned)
    fake_model.respond("fact_check", facts_ok(1, 2, 3))
    fake_model.respond("check_scene_picture", PICTURE_PASSES)
    fault = "The handle becomes a flat strip instead of keeping its rounded shape."
    fake_model.respond(
        "check_clip_product",
        failing(fault),
        failing(fault),
        CLIP_PRODUCT_OK,
        failing(fault),
        failing(fault),
        CLIP_PRODUCT_OK,
    )
    fake_model.respond(
        "transcribe_line",
        *[{"text": " ".join(line.split()[1:]) if index == 2 else line} for index in range(6)],
    )
    fake_model.respond("check_words", {"verdict": "fail", "reason": "A word is missing."})
    fake_model.respond(
        "scene_agent",
        turn(calls=[("draw_starting_picture", PICTURE_FORM)]),
        *(
            turn(
                calls=[("film", {"form": {**FILM["form"], "camera": f"The phone holds angle {i}"}})]
            )
            for i in range(6)
        ),
    )
    producer.told(
        product_page_url,
        ("read_page", {"link": product_page_url, "target_seconds": None}),
        ("plan_ad", {}),
        ("create_person", {}),
        ("run_planning_checks", NO_CHOICES),
        (
            "start_scene_agent",
            {"scene": 1, "instruction": "Film the planned action.", "quotes": [product_page_url]},
        ),
        "I'm making the scene.",
    )
    producer.steps_run("This scene is ready.")

    filmed = results_of("film")
    assert len(filmed) == 6
    assert ["small fixes are done" in result for result in filmed] == [
        False,
        True,
        False,
        False,
        True,
        False,
    ]
    for words in (filmed[1], filmed[4]):
        assert "if this is the same fault" in words.lower()
        assert "different framing or angle" in words
        assert "large and still" in words
        assert "by the hand, or turned away" in words
        assert "simpler action" in words
        assert "say how it targets the fault before filming" in words
    assert "Word check: fail" in filmed[2]
    assert "It passes." in filmed[-1]
    # The guidance leaves a prompt-only try available, including reusing the same picture.
    assert len(handoffs("make_broll_clip")) == 6
    assert len(handoffs("draw_scene_picture")) == 1


@pytest.mark.django_db(transaction=True)
def test_product_fault_streak_follows_its_scene_across_owner_note_runs(
    producer: ScriptedProducer,
    fake_model: FakeModel,
    product_page_url: str,
) -> None:
    from gateway.fake import turn

    from .conftest import NO_CHOICES, READABLE, facts_ok, handoffs, results_of
    from .test_h3_planning import h3_plan
    from .test_producer_auto_fix import failing
    from .test_scene_film import FILM
    from .test_scene_pictures import PICTURE_FORM, PICTURE_PASSES

    planned = h3_plan()
    first_line, second_line = [scene["line"] for scene in planned["plan"]["scenes"][:2]]
    fake_model.respond("check_page", READABLE)
    fake_model.respond("plan_ad", planned)
    fake_model.respond("fact_check", facts_ok(1, 2, 3))
    fake_model.respond("check_scene_picture", PICTURE_PASSES, PICTURE_PASSES)
    fault = "The handle becomes a flat strip instead of keeping its rounded shape."
    fake_model.respond("check_clip_product", failing(fault), failing(fault), failing(fault))
    fake_model.respond(
        "transcribe_line",
        *[{"text": line} for line in [first_line, second_line, first_line, first_line]],
    )
    fake_model.respond(
        "scene_agent",
        turn(calls=[("draw_starting_picture", PICTURE_FORM)]),
        turn(calls=[("film", FILM)]),
        turn(calls=[("give_up", {"reason": "The handle still changes shape."})]),
        turn(calls=[("draw_starting_picture", PICTURE_FORM)]),
        turn(calls=[("film", FILM)]),
        turn(calls=[("give_up", {"reason": "The handle still changes shape."})]),
        turn(calls=[("film", FILM)]),
        turn(calls=[("film", FILM)]),
    )
    producer.told(
        product_page_url,
        ("read_page", {"link": product_page_url, "target_seconds": None}),
        ("plan_ad", {}),
        ("create_person", {}),
        ("run_planning_checks", NO_CHOICES),
        (
            "start_scene_agent",
            {"scene": 1, "instruction": "Film the planned action.", "quotes": [product_page_url]},
        ),
        "I'm making the scene.",
    )
    producer.steps_run("The last try stays on its card.")
    producer.told(
        "Make scene 2",
        (
            "start_scene_agent",
            {"scene": 2, "instruction": "Film the planned action.", "quotes": ["Make scene 2"]},
        ),
        "I'm making scene 2.",
    )
    producer.steps_run("The last try stays on its card.")
    producer.told(
        "Try scene 1 again",
        (
            "pass_note",
            {
                "scenes": [
                    {
                        "scene": 1,
                        "instruction": "Keep the handle rounded.",
                        "quotes": ["Try scene 1 again"],
                    }
                ]
            },
        ),
        "I passed that on.",
    )
    producer.steps_run("Scene 1 is ready.")

    filmed = results_of("film")
    assert len(filmed) == 4
    assert ["small fixes are done" in result for result in filmed] == [False, False, True, False]
    assert len(handoffs("make_broll_clip")) == 4
    assert "It passes." in filmed[-1]
