"""H3 speaks a checked line without a separate voice or line-audio step."""

from collections.abc import Callable
from decimal import Decimal
from typing import Any

import pytest

from gateway.fake import FakeModel, turn
from gateway.models import ModelCall
from jobs.models import Job, ProducedItem, Scene

from .conftest import HeldSteps, WorkerStopped, facts_ok, handoffs, paid_for, results_of, silences
from .test_producer_broll import BROLL_PLAN, calling, run
from .test_scene_film import FILM
from .test_scene_pictures import PICTURE_FORM, picture_checked  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def h3_agent_ready(
    fake_model: FakeModel, page_read: str, say: Callable[..., None], settings: Any
) -> Job:
    settings.H3_VOICE = True
    fake_model.respond("produce", turn(calls=[("plan_ad", {})]), turn(says="Here is the plan."))
    fake_model.respond("plan_ad", BROLL_PLAN)
    say("Plan it")
    job = Job.objects.get()
    assert job.h3_voice
    # Scripted starting records: a checked plan and its portrait, with no voice file.
    Scene.objects.filter(job=job).update(fact_checked=True)
    photo = job.photos.first()
    assert photo is not None
    ProducedItem.objects.create(job=job, kind=ProducedItem.Kind.PORTRAIT, file=photo.file)
    return job


@pytest.mark.parametrize("restart", [False, True])
def test_h3_scene_films_without_line_audio_and_transcribes_its_own_sound(
    fake_model: FakeModel,
    h3_agent_ready: Job,
    picture_checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    restart: bool,
) -> None:
    from agents import tasks

    line = h3_agent_ready.scenes.get(number=2).line
    fake_model.respond("transcribe_line", {"text": line})
    fake_model.respond(
        "scene_agent",
        turn(calls=[("draw_starting_picture", PICTURE_FORM)]),
        turn(
            calls=[
                ("film", {"form": {**FILM["form"], "after_last_word": "The hand lifts the mug"}})
            ]
        ),
        turn(calls=[("give_up", {"reason": "This clip should have passed."})]),
    )
    calling(
        fake_model,
        say,
        ("start_scene_agent", {"scene": 2, "instruction": "Show the pour.", "quotes": ["go on"]}),
    )
    if restart:
        fake_model.respond("collect_clip", WorkerStopped())
        with pytest.raises(WorkerStopped):
            steps.run_held()
        tasks.carry_on_after_the_worker_starts()
    run(fake_model, steps)
    sent = handoffs("make_broll_clip")
    assert len(sent) == 1, results_of("start_scene_agent")
    assert sent[0]["seconds"] == 5
    assert sent[0]["prompt_enhancement"] is False
    assert f"<d>[English] {line}</d>" in sent[0]["prompt"]
    assert h3_agent_ready.person_voice in sent[0]["prompt"]
    assert "Off camera" in sent[0]["prompt"]
    assert "No speech" not in sent[0]["prompt"]
    assert "No music" in sent[0]["prompt"]
    assert not {"design_voice", "speak_line", "measure_voice"} & set(paid_for())
    assert not ProducedItem.objects.filter(kind=ProducedItem.Kind.LINE_AUDIO).exists()
    clip = ProducedItem.objects.get(kind=ProducedItem.Kind.CLIP)
    transcript = ProducedItem.objects.get(kind=ProducedItem.Kind.TRANSCRIPT)
    assert clip.made_from is None
    assert transcript.made_from == clip
    assert transcript.text == line
    collected = ModelCall.objects.get(purpose="collect_broll_clip")
    assert collected.output is not None
    assert clip.file == collected.output["file"]
    transcribed = ModelCall.objects.get(purpose="transcribe_line")
    # The fake's 5 s AAC track decodes to 5.12 s, billed at Scribe's $0.22/hour.
    assert transcribed.cost_usd == Decimal("0.000313")
    assert len(fake_model.transcribed) == 1
    assert paid_for().count("transcribe_line") == 1
    assert fake_model.spoken == []
    assert silences(fake_model.transcribed[0]) == []
    assert "passes" in str(results_of("film"))


def test_changed_h3_line_is_checked_without_being_voiced_in_advance(
    fake_model: FakeModel,
    h3_agent_ready: Job,
    picture_checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    from .test_scene_agent_changes import change

    line = "This mug holds three hundred and fifty millilitres."
    fake_model.respond("fact_check", facts_ok(2))
    fake_model.respond("transcribe_line", {"text": line})
    fake_model.respond(
        "scene_agent",
        turn(
            calls=[
                change(
                    line=line,
                    kind_why="The small volume marking needs a close-up beyond arm's length.",
                )
            ]
        ),
        turn(calls=[("draw_starting_picture", PICTURE_FORM)]),
        turn(calls=[("film", FILM)]),
        turn(calls=[("give_up", {"reason": "The clip should pass."})]),
    )
    calling(
        fake_model,
        say,
        ("start_scene_agent", {"scene": 2, "instruction": "Use this line.", "quotes": ["go on"]}),
    )
    run(fake_model, steps)
    assert "The scene row changed" in str(results_of("change_scene"))
    assert handoffs("fact_check")[-1]["lines"][0]["line"] == line
    assert f"<d>[English] {line}</d>" in handoffs("make_broll_clip")[0]["prompt"]
    assert fake_model.spoken == []
    assert "passes" in str(results_of("film"))
