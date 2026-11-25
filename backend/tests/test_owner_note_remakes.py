"""Owner-note remakes use the same clip checks as the first clip (#693)."""

from collections.abc import Callable

import pytest
from django.db.models.signals import post_save
from rest_framework.test import APIClient

from agents import tasks
from gateway.fake import FakeModel, turn
from jobs.models import AgentFixTry, ProducedItem

from .conftest import HeldSteps, WorkerStopped, handoffs, paid_for, results_of
from .scene_script import make_scene_clip, script_scene_agents
from .test_producer_broll import calling, checked, run  # noqa: F401
from .test_scene_agent import scene_agent_ready  # noqa: F401
from .test_scene_film import FILM
from .test_scene_pictures import PICTURE_FORM, picture_checked  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)


def test_an_owner_note_remake_passes_on_the_clip_judge_alone(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    picture_checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    api: APIClient,
    session_id: str,
) -> None:
    fake_model.respond(
        "scene_agent",
        turn(calls=[("draw_starting_picture", PICTURE_FORM)]),
        turn(calls=[("film", FILM)]),
    )
    calling(
        fake_model,
        say,
        ("start_scene_agent", {"scene": 2, "instruction": "Show the pour.", "quotes": ["Go on"]}),
    )
    run(fake_model, steps)
    old = ProducedItem.objects.get(kind="clip", scene__number=2)
    assert old.ruling is not None
    old_pictures = old.ruling["pictures"]
    old_keys = {picture["key"] for name in ("sides", "frames") for picture in old_pictures[name]}
    before = len(fake_model.asked)

    note = "Pour from the right this time."
    fake_model.respond(
        "produce",
        turn(
            calls=[
                (
                    "pass_note",
                    {
                        "scenes": [
                            {
                                "scene": 2,
                                "instruction": note,
                                "quotes": [note],
                                "owner_asked": True,
                                "new_asked_change": note,
                            }
                        ]
                    },
                )
            ]
        ),
        turn(says="I passed it on."),
    )
    fake_model.respond(
        "scene_agent",
        turn(calls=[("draw_starting_picture", PICTURE_FORM)]),
        turn(calls=[("film", FILM)]),
    )
    say(note)
    run(fake_model, steps)

    assert "It passes." in results_of("film")[-1]
    assert "check_clip_motion" not in paid_for()
    remake = AgentFixTry.objects.latest("pk")
    assert remake.passed and remake.fix_check is None
    assert remake.tool_call.pictures
    assert "The judge's verdict: pass." in remake.tool_call.result
    assert "Whether the owner's change is made" not in remake.tool_call.result
    assert remake.clip is not None and remake.clip.ruling is not None
    assert remake.clip.ruling["checks"] == {"product": "pass", "pass_through": "pass"}
    assert "compared_with" not in remake.clip.ruling
    assert old.ruling == ProducedItem.objects.get(pk=old.pk).ruling
    judges = [r for r in fake_model.asked[before:] if r.purpose.startswith("check_clip_")]
    assert {r.purpose for r in judges} == {
        "check_clip_references",
        "check_clip_product",
        "check_clip_pass_through",
    }
    for request in judges:
        assert not old_keys.intersection(image.key for image in request.images)
        assert "owner_change" not in request.output.model_fields
        assert not {"owner_change", "owner_quotes", "producer_instruction"}.intersection(
            request.handoff.model_dump()
        )
    card = next(c for c in api.get(f"/api/sessions/{session_id}/scenes/").json() if c["scene"] == 2)
    assert card["versions"][-1]["verdict"] == "pass"
    assert note in str(handoffs("scene_agent")[-1])


def test_a_note_about_matching_two_scenes_passes_both_remakes(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    api: APIClient,
    session_id: str,
) -> None:
    """Manscaped regression (#684): one clip need not prove what another scene shows."""
    for number in (2, 3):
        make_scene_clip(fake_model, steps, say, scene=number)
    first_clips = set(ProducedItem.objects.filter(kind="clip").values_list("pk", flat=True))
    note = "Use the same mug at the same desk in scenes 2 and 3: first pour, then sip."
    fake_model.respond(
        "produce",
        turn(
            calls=[
                (
                    "pass_note",
                    {
                        "scenes": [
                            {
                                "scene": number,
                                "instruction": instruction,
                                "quotes": [note],
                                "owner_asked": True,
                                "new_asked_change": note,
                            }
                            for number, instruction in (
                                (2, "Pour into the mug at the desk."),
                                (3, "Sip from that same mug at that same desk."),
                            )
                        ]
                    },
                )
            ]
        ),
        turn(says="I passed the note to both scenes."),
    )
    script_scene_agents(fake_model, scenes=2)
    say(note)
    run(fake_model, steps)

    remakes = AgentFixTry.objects.exclude(clip_id__in=first_clips).order_by("pk")
    assert [row.passed for row in remakes] == [True, True]
    assert all(row.fix_check is None for row in remakes)
    assert "check_clip_motion" not in paid_for()
    assert paid_for().count("make_broll_clip") == 4
    assert results_of("film")[-2:] and all("It passes." in t for t in results_of("film")[-2:])
    cards = api.get(f"/api/sessions/{session_id}/scenes/").json()
    assert [c["versions"][-1]["verdict"] for c in cards if c["scene"] in (2, 3)] == ["pass", "pass"]


def test_resuming_an_old_try_ignores_its_stored_fix_check_failure(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    make_scene_clip(fake_model, steps, say)
    script_scene_agents(fake_model)
    calling(
        fake_model,
        say,
        (
            "pass_note",
            {
                "scenes": [
                    {
                        "scene": 2,
                        "instruction": "Pour from the right.",
                        "quotes": ["Go on"],
                        "owner_asked": True,
                        "new_asked_change": "Go on",
                    }
                ]
            },
        ),
    )

    def stop_after_clip_saved(instance: AgentFixTry, **_: object) -> None:
        if instance.clip_id is not None:
            raise WorkerStopped()

    post_save.connect(stop_after_clip_saved, sender=AgentFixTry)
    try:
        with pytest.raises(WorkerStopped):
            steps.run_next()
    finally:
        post_save.disconnect(stop_after_clip_saved, sender=AgentFixTry)
    saved = AgentFixTry.objects.latest("pk")
    # A checkpoint made before #693 could have saved a failed fix check here.
    stored = {"verdict": "failed", "reason": "The change was not made."}
    AgentFixTry.objects.filter(pk=saved.pk).update(fix_check=stored)
    fake_model.answer_unscripted("produce", lambda _: turn(says="The scene is ready."))

    tasks.carry_on_after_the_worker_starts()
    steps.run_held()

    saved.refresh_from_db()
    assert saved.passed and saved.fix_check == stored
    assert "It passes." in results_of("film")[-1]
    assert "Whether the owner's change is made" not in results_of("film")[-1]
    assert paid_for().count("make_broll_clip") == 2
    assert "check_clip_motion" not in paid_for()
