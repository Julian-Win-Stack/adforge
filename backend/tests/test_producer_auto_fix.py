"""Door 1 of the fixing agent, driven through the chat: a B-roll clip the clip judge fails is
handed to the fixing agent at once, which films the scene again try after try until the
judge passes one; every try is a version of the scene, the one passed is its clip, and the
producer is told once, when it's done. The producer's model, the scene models, the video
model and the judge are faked at the gateway, and the agent by tests/agent_tries.py (spec
"Fixing agent", Julian's yes 2026-10-19 16:49: "we will always start with the agent")."""

from collections.abc import Callable
from typing import Any

import pytest
from django.test import override_settings

from agents import fixing_agent
from chat.models import Attachment, Message
from gateway.fake import FakeModel
from jobs import work
from jobs.models import AgentFix, ProducedItem, Scene, SceneStep
from jobs.notices import post_notice

from .agent_tries import agent_films, agent_stops
from .conftest import CLIP_MOTION_OK, HeldSteps, paid_for
from .test_producer_broll import (
    calling,
    checked,  # noqa: F401 (a fixture)
    clips_asked,
    instructed,  # noqa: F401 (a fixture)
    made_ready,
    run,
    sent_prompt,
)

pytestmark = pytest.mark.django_db(transaction=True)

FLOATS = "The teapot rises at 1.2 s with no hand on it. A real one stays on the bench."
SPINS = "The mug spins at 2 s and shows its back. A real one keeps its front to the camera."
REMADE = "A hand tips the teapot and tea pours into the mug while the teapot stays in the hand."
REMADE_AGAIN = "A hand holds the teapot by its handle and pours tea into the mug."
# Owner notes on scene 2, as other tests send them.
HOVERS = "The hand only hovers over the mug; it should pour the tea into it."
NOTED = "Tea pours from the teapot into the mug and steam rises from it."


def failing(problem: str, question: str = "real_motion") -> dict[str, Any]:
    """MOTION's answer when it sees `problem`."""
    return {
        **CLIP_MOTION_OK,
        question: {"verdict": "fail", "problem": problem, "back_to_normal_at": "never"},
    }


def rewrite(prompt: str) -> dict[str, Any]:
    return {"motion_prompt": prompt, "motion_prompt_reason": "It keeps the teapot in the hand."}


def clip_of_scene_2(fake_model: FakeModel, steps: HeldSteps, say: Callable[..., None]) -> SceneStep:
    """Have the producer make scene 2's clip, showing the mug; gives its step."""
    made_ready(fake_model, steps, say, (2,))
    calling(fake_model, say, ("make_clip", {"scene": 2}))
    run(fake_model, steps)
    return SceneStep.objects.filter(kind="clip", scene__number=2).order_by("id")[0]


def clips() -> list[int]:
    """The version of each of scene 2's clips, oldest first."""
    return list(
        ProducedItem.objects.filter(kind="clip", scene__number=2)
        .order_by("id")
        .values_list("version", flat=True)
    )


def shown_clips() -> list[str]:
    """The file of each clip shown in the chat, oldest first."""
    return list(
        Attachment.objects.filter(kind="video", message__role=Message.Role.AGENT)
        .order_by("id")
        .values_list("file", flat=True)
    )


def labels() -> list[str]:
    return [version.label for version in work.versions(Scene.objects.get(number=2))]


# --- The judge fails the first clip; the agent's second try passes -------------------------


@pytest.fixture
def fixed_on_try_2(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    monkeypatch: pytest.MonkeyPatch,
) -> SceneStep:
    """Scene 2's first clip fails on the teapot floating; the agent's first try fails on the
    mug spinning, and its second passes."""
    fake_model.respond(
        "check_clip_motion",
        failing(FLOATS),
        failing(SPINS),
    )
    agent_films(monkeypatch, REMADE, REMADE_AGAIN)
    return clip_of_scene_2(fake_model, steps, say)


# Fixtures other tests use: the agent's first try passes, or it gives up after two.
@pytest.fixture
def remade_once(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    monkeypatch: pytest.MonkeyPatch,
) -> SceneStep:
    """Scene 2's first clip fails on the teapot floating; the agent's first try passes."""
    fake_model.respond("check_clip_motion", failing(FLOATS))
    agent_films(monkeypatch, REMADE)
    return clip_of_scene_2(fake_model, steps, say)


@pytest.fixture
def remade_twice(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    monkeypatch: pytest.MonkeyPatch,
) -> SceneStep:
    """Scene 2's first clip fails on the teapot floating, and each of the agent's two tries on
    the mug spinning; then it gives up, picking its first try."""
    fake_model.respond(
        "check_clip_motion",
        failing(FLOATS),
        failing(SPINS),
        failing(SPINS),
    )
    agent_films(monkeypatch, REMADE, REMADE_AGAIN, picks=1)
    return clip_of_scene_2(fake_model, steps, say)


def test_a_try_that_changes_what_the_scene_shows_and_passes_finishes_the_scene(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The agent films its try of a scene it changed: that try is made for the scene as it
    # stands, so it isn't stale (spec #247 point 9), whatever the step it fixes was made for.
    fake_model.respond("check_clip_motion", failing(FLOATS))
    agent_films(monkeypatch, REMADE, shows={1: "Tea pours from a teapot into the mug."})

    step = clip_of_scene_2(fake_model, steps, say)

    step.refresh_from_db()
    assert "Scene 2 is finished." in step.result
    assert "stale" not in step.result
    assert Scene.objects.get(number=2).status == "finished"


def test_a_failed_clip_is_handed_to_the_fixing_agent_at_once(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_model.respond("check_clip_motion", failing(FLOATS))
    agent = agent_films(monkeypatch, REMADE)

    step = clip_of_scene_2(fake_model, steps, say)

    ((handed, door, failure, cap),) = agent.handed
    first = ProducedItem.objects.filter(kind="clip", scene__number=2).order_by("id").first()
    assert first is not None
    assert (handed, door, cap) == (step, "judge", fixing_agent.MOST_TRIES)
    assert failure.ruling == first.ruling
    # No reword or remake by code comes first.
    assert "remake_clip_prompt" not in paid_for()


def test_every_try_is_a_version_and_the_one_the_judge_passed_is_the_scenes_clip(
    fixed_on_try_2: SceneStep,
) -> None:
    first, try_1, try_2 = ProducedItem.objects.filter(kind="clip").order_by("id")
    assert (first.remake, try_1.remake, try_2.remake) == (None, 1, 2)
    assert [clip.ruling["verdict"] for clip in (first, try_1, try_2) if clip.ruling] == [
        "fail",
        "fail",
        "pass",
    ]
    assert labels() == ["v1 first version (first clip)", "v2 try 1", "v3 try 2"]
    assert [version.clip for version in work.versions(Scene.objects.get(number=2))] == [
        first,
        try_1,
        try_2,
    ]
    # Each try filmed as the agent asked, from the same sent list (spec #210: code's speed
    # line and labels, then the agent's words; no starting picture).
    assert clips_asked("prompt") == [sent_prompt(), sent_prompt(REMADE), sent_prompt(REMADE_AGAIN)]
    assert AgentFix.objects.get().passed == try_2


def test_only_the_try_the_judge_passed_is_shown_and_the_producer_told_once(
    fixed_on_try_2: SceneStep,
) -> None:
    passed = ProducedItem.objects.filter(kind="clip").order_by("id").last()
    assert passed is not None
    assert shown_clips() == [passed.file]
    (shown,) = Attachment.objects.filter(kind="video", message__role=Message.Role.AGENT)
    assert shown.version == "v3 try 2"
    assert fixed_on_try_2.status == "finished"
    assert (
        f'The clip judge failed the first clip it made ("{FLOATS}"), so the fixing agent filmed '
        "it again, and the judge passed try 2. Scene 2 is finished."
    ) in fixed_on_try_2.result
    # Nothing to pick from: the chat shows no tries.
    assert not Message.objects.filter(tries_of__isnull=False).exists()


def test_a_clip_the_judge_passes_isnt_handed_to_the_agent(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = agent_films(monkeypatch, REMADE)

    step = clip_of_scene_2(fake_model, steps, say)

    assert agent.handed == []
    assert paid_for().count("make_broll_clip") == 1
    assert clips() == [1]
    assert labels() == ["v1 first version"]
    assert "judge" not in step.result


def test_a_talking_scene_is_never_judged_or_handed_to_the_agent(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Answers that would fail a clip, were one judged.
    fake_model.respond("check_clip_motion", failing(FLOATS))
    agent = agent_films(monkeypatch, REMADE)
    made_ready(fake_model, steps, say, (1,))
    calling(fake_model, say, ("make_clip", {"scene": 1}))
    run(fake_model, steps)

    assert paid_for().count("make_talking_clip") == 1
    assert not [purpose for purpose in paid_for() if purpose.startswith("check_clip_")]
    assert agent.handed == []
    step = SceneStep.objects.get(kind="clip", scene__number=1)
    assert (step.status, "judge" in step.result) == ("finished", False)
    # Never judged, the clip carries no ruling.
    assert ProducedItem.objects.get(kind="clip", scene__number=1).ruling is None


@override_settings(QUALITY_CHECKS=False)
def test_with_quality_checks_off_no_judge_or_agent_runs(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_model.respond("check_clip_motion", failing(FLOATS))
    agent = agent_films(monkeypatch, REMADE)

    clip_of_scene_2(fake_model, steps, say)

    assert not [purpose for purpose in paid_for() if purpose.startswith("check_clip_")]
    assert agent.handed == []
    assert clips() == [1]


def test_an_agent_that_cant_carry_on_keeps_the_first_clip_and_finishes_the_step(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_model.respond("check_clip_motion", failing(FLOATS))
    agent_stops(monkeypatch, RuntimeError("the model fell over"))

    step = clip_of_scene_2(fake_model, steps, say)

    assert (step.status, clips()) == ("finished", [1])
    assert (
        f'The clip judge failed the first clip it made ("{FLOATS}"), and it couldn\'t be made '
        "again, so it is kept as it is."
    ) in step.result
    assert not AgentFix.objects.exists()


def test_a_step_run_again_reads_back_how_the_agent_ended_and_never_hands_it_over_twice(
    fixed_on_try_2: SceneStep, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent = agent_films(monkeypatch, REMADE)

    again = work.make_clip(fixed_on_try_2)

    assert again == AgentFix.objects.get().passed
    assert agent.handed == []


# --- Jobs made before the fixing agent: the old auto-fix's remakes -------------------------

FAILED = {"verdict": "fail", "failed_answers": 2, "failures": [], "checks": {}, "answers": {}}
PASSED = {**FAILED, "verdict": "pass", "failed_answers": 0}


def old_remakes(*rulings: dict[str, Any]) -> list[ProducedItem]:
    """Scene 2's clip, made before the fixing agent: the judge failed it, and the old
    auto-fix (#155) remade it once for each of `rulings`, each remake kept in the clip's own
    step. Gives the first clip and each remake."""
    first = ProducedItem.objects.get(kind="clip", scene__number=2)
    ProducedItem.objects.filter(pk=first.pk).update(ruling=FAILED)
    assert first.scene is not None
    remakes = [
        ProducedItem.objects.create(
            job=first.job,
            scene=first.scene,
            step=first.step,
            kind=ProducedItem.Kind.CLIP,
            version=work._next_version(first.scene, ProducedItem.Kind.CLIP),
            file=first.file,
            seconds=first.seconds,
            made_from=first.made_from,
            picture=first.picture,
            remake=number,
            ruling=ruling,
        )
        for number, ruling in enumerate(rulings, start=1)
    ]
    first.refresh_from_db()
    return [first, *remakes]


def test_an_old_jobs_remakes_are_no_versions_but_the_one_kept(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    clip_of_scene_2(fake_model, steps, say)
    *_, kept = old_remakes(FAILED, PASSED)

    assert labels() == ["v1 first version"]
    assert [version.clip for version in work.versions(Scene.objects.get(number=2))] == [kept]


def test_an_old_jobs_remakes_shown_to_pick_from_are_each_a_version(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    step = clip_of_scene_2(fake_model, steps, say)
    made = old_remakes(FAILED, FAILED)
    post_notice(step.scene.job, "Watch them.", Message.Level.INFO, tries_of=step.pk)

    assert [version.clip for version in work.versions(Scene.objects.get(number=2))] == made
