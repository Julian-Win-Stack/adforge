"""First clips and retries belong to one scene-agent chat. External models are scripted;
real scene tools draw, film and judge each try. Explicit historical checkpoint fixtures
remain for old ads' cards, versions and fixing records.
"""

from collections.abc import Callable
from typing import Any

import pytest
from django.test import override_settings

from chat.models import Attachment, Message
from gateway.fake import FakeModel
from jobs import notices, work
from jobs.models import ProducedItem, Scene, SceneStep
from jobs.notices import post_notice

from .conftest import CLIP_PRODUCT_OK, HeldSteps, paid_for
from .legacy_records import (
    legacy_assemble_through_the_chat as legacy_assemble_through_the_chat,
)  # noqa: F401
from .legacy_records import (
    legacy_clip_of_scene_2 as legacy_clip_of_scene_2,
)
from .legacy_records import seed_legacy_fix
from .test_producer_broll import (
    calling,
    checked,  # noqa: F401 (a fixture)
    instructed,  # noqa: F401 (a fixture)
    made_ready,
    run,
)

pytestmark = [pytest.mark.django_db(transaction=True)]

FLOATS = "The teapot rises at 1.2 s with no hand on it. A real one stays on the bench."
SPINS = "The mug spins at 2 s and shows its back. A real one keeps its front to the camera."
REMADE = "A hand tips the teapot and tea pours into the mug while the teapot stays in the hand."
REMADE_AGAIN = "A hand holds the teapot by its handle and pours tea into the mug."
# Owner notes on scene 2, as other tests send them.
HOVERS = "The hand only hovers over the mug; it should pour the tea into it."
NOTED = "Tea pours from the teapot into the mug and steam rises from it."


def failing(problem: str) -> dict[str, Any]:
    """PRODUCT's answer when it sees a fault."""
    return {**CLIP_PRODUCT_OK, "verdict": "fail", "fault": problem}


def rewrite(prompt: str) -> dict[str, Any]:
    return {"motion_prompt": prompt, "motion_prompt_reason": "It keeps the teapot in the hand."}


def clip_of_scene_2(fake_model: FakeModel, steps: HeldSteps, say: Callable[..., None]) -> SceneStep:
    """Compatibility name for the explicitly historical scene-2 clip fixture."""
    return legacy_clip_of_scene_2(fake_model, steps, say)


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


def start_live_scene(
    fake_model: FakeModel,
    steps: HeldSteps,
    say: Callable[..., None],
) -> SceneStep:
    """The producer starts the live scene agent after the chat prepares its audio."""
    from .test_scene_pictures import PICTURE_PASSES

    made_ready(fake_model, steps, say, (2,))
    fake_model.answer_unscripted(
        "transcribe_line", lambda _: {"text": Scene.objects.get(number=2).line}
    )
    fake_model.answer_unscripted("check_scene_picture", lambda _: PICTURE_PASSES)
    calling(
        fake_model,
        say,
        (
            "start_scene_agent",
            {
                "scene": 2,
                "instruction": "Show the planned action.",
                "quotes": ["Go on"],
            },
        ),
    )
    return SceneStep.objects.get(kind=SceneStep.Kind.SCENE_AGENT, scene__number=2)


@pytest.fixture
def fixed_on_try_2(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> SceneStep:
    """Three clips in one scene chat; the first two fail and the third passes."""
    from gateway.fake import turn

    from .test_scene_film import FILM
    from .test_scene_pictures import PICTURE_FORM

    fake_model.respond("check_clip_product", failing(FLOATS), failing(SPINS))
    fake_model.respond(
        "scene_agent",
        turn(calls=[("draw_starting_picture", PICTURE_FORM)]),
        turn(calls=[("film", FILM)]),
        turn(calls=[("film", {"form": {**FILM["form"], "camera": "The phone tracks the hand"}})]),
        turn(calls=[("film", {"form": {**FILM["form"], "camera": "The phone drifts left"}})]),
    )
    step = start_live_scene(fake_model, steps, say)
    run(fake_model, steps)
    step.refresh_from_db()
    return step


# Historical fixing-agent fixtures imported by old-record card and round tests.
def historical_ruling(problem: str | None = None) -> dict[str, Any]:
    """An already-stored ruling from before #350, never re-settled by today's judge."""
    from .test_clip_judge import motion

    answer = motion(real_motion=problem) if problem else motion()
    return {
        "verdict": "fail" if problem else "pass",
        "checks": {
            "product": "pass",
            **{
                key: answer[key]["verdict"]
                for key in ("real_motion", "sudden_change", "does_what_was_ordered")
            },
        },
        "failed_answers": 1 if problem else 0,
        "failures": [{"check": "motion", "ask": 1, "problems": [problem], "answer": answer}]
        if problem
        else [],
        "answers": {"product": [CLIP_PRODUCT_OK], "motion": [answer]},
    }


@pytest.fixture
def remade_once(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> SceneStep:
    """A recorded historical failed first clip and one successful old-agent try."""
    step = legacy_clip_of_scene_2(fake_model, steps, say)
    seed_legacy_fix(
        step,
        prompts=[REMADE],
        rulings=[historical_ruling()],
        first_ruling=historical_ruling(FLOATS),
    )
    return step


@pytest.fixture
def remade_twice(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> SceneStep:
    """Recorded historical failed tries, with the last kept and first marked as its pick."""
    step = legacy_clip_of_scene_2(fake_model, steps, say)
    seed_legacy_fix(
        step,
        tries=2,
        prompts=[REMADE, REMADE_AGAIN],
        rulings=[historical_ruling(SPINS), historical_ruling(SPINS)],
        first_ruling=historical_ruling(FLOATS),
        picked=1,
        reason="Every try kept the same fault.",
    )
    return step


def test_failed_clips_are_fixed_in_the_scene_agents_same_chat(
    fixed_on_try_2: SceneStep,
    fake_model: FakeModel,
) -> None:
    from .conftest import handoffs

    conversations = handoffs("scene_agent")
    assert FLOATS in str(conversations[2])
    assert SPINS in str(conversations[3])
    assert not [request for request in fake_model.turns_taken if request.purpose == "fix_scene"]
    assert "remake_clip_prompt" not in paid_for()
    assert "rewrite_clip_prompt" not in paid_for()
    assert "passed" in fixed_on_try_2.result


def test_each_filmed_try_is_a_card_version_with_its_starting_picture(
    fixed_on_try_2: SceneStep,
    api: Any,
    session_id: str,
) -> None:
    card = next(
        each for each in api.get(f"/api/sessions/{session_id}/scenes/").json() if each["scene"] == 2
    )
    assert [each["verdict"] for each in card["versions"]] == ["fail", "fail", "pass"]
    assert [each["in_ad"] for each in card["versions"]] == [False, False, True]
    assert len({each["picture"] for each in card["versions"]}) == 1
    assert all(each["sent"]["mode"] == "start_frame" for each in card["versions"])
    assert all(each["sent"]["pictures"] == [] for each in card["versions"])
    assert paid_for().count("draw_scene_picture") == 1
    assert paid_for().count("make_broll_clip") == 3


def test_the_producer_is_told_once_when_the_scene_passes(
    fixed_on_try_2: SceneStep,
) -> None:
    from .conftest import handoffs

    ended = [
        event
        for request in handoffs("produce")
        for event in request["conversation"]
        if event.get("kind") == "step_finished"
        and "scene 2's agent passed" in event.get("text", "")
    ]
    assert len(ended) == 1
    assert "cost" in ended[0]["text"].lower()


def test_a_first_clip_that_passes_finishes_its_scene_agent(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    from .scene_script import script_scene_agents

    script_scene_agents(fake_model)
    step = start_live_scene(fake_model, steps, say)
    run(fake_model, steps)
    step.refresh_from_db()
    assert "passed" in step.result
    assert paid_for().count("make_broll_clip") == 1
    assert paid_for().count("draw_scene_picture") == 1
    assert len([turn for turn in fake_model.turns_taken if turn.purpose == "scene_agent"]) == 2


def test_a_talking_scene_runs_its_word_and_clip_checks_in_its_agent(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    from .scene_script import make_scene_clip

    make_scene_clip(fake_model, steps, say, scene=1)

    assert paid_for().count("make_broll_clip") == 1
    assert paid_for().count("transcribe_line") == 1
    assert paid_for().count("check_clip_motion") == 1
    assert any(turn.purpose == "scene_agent" for turn in fake_model.turns_taken)


@override_settings(QUALITY_CHECKS=False)
def test_checks_off_still_runs_the_scene_agent_and_stops_after_its_first_clip(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    from .scene_script import script_scene_agents

    script_scene_agents(fake_model)
    step = start_live_scene(fake_model, steps, say)
    run(fake_model, steps)
    step.refresh_from_db()
    assert "passed" in step.result
    assert not [purpose for purpose in paid_for() if purpose.startswith("check_clip_")]
    assert "check_scene_picture" not in paid_for()
    assert paid_for().count("make_broll_clip") == 1
    assert len([turn for turn in fake_model.turns_taken if turn.purpose == "scene_agent"]) == 2


def test_a_scene_agent_give_up_keeps_its_filmed_try_and_offers_a_way_out(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    api: Any,
    session_id: str,
) -> None:
    from gateway.fake import turn

    from .conftest import chat
    from .test_scene_film import FILM
    from .test_scene_pictures import PICTURE_FORM

    fake_model.respond("check_clip_product", failing(FLOATS))
    fake_model.respond(
        "scene_agent",
        turn(calls=[("draw_starting_picture", PICTURE_FORM)]),
        turn(calls=[("film", FILM)]),
        turn(calls=[("give_up", {"reason": "A needed side is unseen."})]),
    )
    step = start_live_scene(fake_model, steps, say)
    run(fake_model, steps)
    step.refresh_from_db()
    assert "gave up" in step.result
    shown = str(chat(api, session_id))
    assert "last clip stays" in shown
    assert "holds it and talks about it" in shown and "send a photo" in shown
    card = next(
        each for each in api.get(f"/api/sessions/{session_id}/scenes/").json() if each["scene"] == 2
    )
    assert len(card["versions"]) == 1 and card["versions"][0]["in_ad"]


def test_worker_restart_keeps_the_first_paid_clip_and_reads_the_failure_before_retrying(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    from agents import tasks
    from gateway.fake import turn

    from .conftest import WorkerStopped, handoffs
    from .test_scene_film import FILM
    from .test_scene_pictures import PICTURE_FORM

    fake_model.respond("check_clip_product", failing(FLOATS))
    fake_model.respond(
        "scene_agent",
        turn(calls=[("draw_starting_picture", PICTURE_FORM)]),
        turn(calls=[("film", FILM)]),
        WorkerStopped(),
    )
    start_live_scene(fake_model, steps, say)
    with pytest.raises(WorkerStopped):
        steps.run_held()
    assert paid_for().count("make_broll_clip") == 1
    fake_model.respond("scene_agent", turn(calls=[("film", FILM)]))
    fake_model.respond("produce", turn(says="The scene is ready."))
    tasks.carry_on_after_the_worker_starts()
    steps.run_held()
    assert paid_for().count("make_broll_clip") == 2
    assert FLOATS in str(handoffs("scene_agent")[-1])


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
    post_notice(
        step.scene.job, notices.TRIES_TO_PICK, "Watch them.", Message.Level.INFO, tries_of=step.pk
    )

    assert [version.clip for version in work.versions(Scene.objects.get(number=2))] == made
