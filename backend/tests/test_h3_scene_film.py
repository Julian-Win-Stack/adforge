"""H3 speaks a checked line without a separate voice or line-audio step."""

from collections.abc import Callable
from decimal import Decimal
from typing import Any

import pytest

from gateway.fake import FakeModel, turn
from gateway.models import ModelCall
from jobs.models import Job, ProducedItem, Scene
from jobs.pace import h3_clip_seconds

from .conftest import HeldSteps, WorkerStopped, facts_ok, handoffs, paid_for, results_of, silences
from .test_h3_planning import h3_plan
from .test_producer_broll import LINE_2, calling, run
from .test_scene_film import FILM
from .test_scene_pictures import PICTURE_FORM, picture_checked  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def h3_agent_ready(
    fake_model: FakeModel, page_read: str, say: Callable[..., None], settings: Any
) -> Job:
    fake_model.respond("produce", turn(calls=[("plan_ad", {})]), turn(says="Here is the plan."))
    plan = h3_plan()
    plan["plan"]["scenes"][1]["line"] = LINE_2 + " At home, every day."
    fake_model.respond("plan_ad", plan)
    say("Plan it")
    job = Job.objects.get()
    # Scripted starting records: a checked plan and its portrait, with no voice file.
    Scene.objects.filter(job=job).update(fact_checked=True)
    photo = job.photos.first()
    assert photo is not None
    ProducedItem.objects.create(job=job, kind=ProducedItem.Kind.PORTRAIT, file=photo.file)
    return job


@pytest.mark.parametrize("restart", [False, True])
@pytest.mark.parametrize("kind", [Scene.Kind.BROLL, Scene.Kind.TALKING])
def test_h3_scene_films_without_line_audio_and_transcribes_its_own_sound(
    fake_model: FakeModel,
    h3_agent_ready: Job,
    picture_checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    restart: bool,
    kind: str,
) -> None:
    from agents import tasks

    talking = kind == Scene.Kind.TALKING
    Scene.objects.filter(job=h3_agent_ready, number=2).update(
        kind=kind,
        kind_why="" if talking else "The view inside needs a close shot.",
        person_shown=Scene.PersonShown.HAS_FACE if talking else Scene.PersonShown.NO_FACE,
    )
    picture_form = (
        {
            **PICTURE_FORM,
            "person_and_room": "The presenter at the desk, her face and both hands visible",
            "pose": "She holds the mug beside her face with the handle visible",
            "bodies": "One woman, with her face and both hands visible",
        }
        if talking
        else PICTURE_FORM
    )
    line = h3_agent_ready.scenes.get(number=2).line
    fake_model.respond("transcribe_line", {"text": line})
    fake_model.respond(
        "scene_agent",
        turn(calls=[("draw_starting_picture", picture_form)]),
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
    # The line at 2.8 words a second and 0.6 seconds of action after it, rounded up.
    assert sent[0]["seconds"] == h3_clip_seconds(len(line.split()))
    assert sent[0]["prompt_enhancement"] is False
    assert f"<d>[English] {line}</d>" in sent[0]["prompt"]
    assert h3_agent_ready.person_voice.rstrip(".") in sent[0]["prompt"]
    assert ("She talks straight to the lens" if talking else "Off camera") in sent[0]["prompt"]
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
    # The fake's 5 s AAC track decodes to about 5 s, billed at Scribe's $0.22/hour.
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

    line = "This mug holds three hundred and fifty millilitres of tea every day."
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


def test_video_quotes_go_back_to_the_agent_before_any_h3_request(
    producer: Any,
    fake_model: FakeModel,
    product_page_url: str,
) -> None:
    from agents.scene_forms import VideoForm

    from .conftest import NO_CHOICES, READABLE
    from .test_scene_pictures import PICTURE_PASSES

    planned = h3_plan()
    voice = "An adult woman's voice with a warm tone and a steady pace."
    planned["plan"]["person_voice"] = voice
    line = planned["plan"]["scenes"][0]["line"]
    fake_model.respond("check_page", READABLE)
    fake_model.respond("plan_ad", planned)
    fake_model.respond("fact_check", facts_ok(1, 2, 3))
    fake_model.respond("check_scene_picture", PICTURE_PASSES)
    fake_model.respond("transcribe_line", {"text": line})
    quoted_forms = [
        {
            "form": {
                **FILM["form"],
                field: ['A banner reads "Save today"']
                if field == "beats"
                else 'A banner reads "Save today"',
            }
        }
        for field in VideoForm.model_fields
    ]
    fake_model.respond(
        "scene_agent",
        turn(calls=[("draw_starting_picture", PICTURE_FORM)]),
        *(turn(calls=[("film", form)]) for form in quoted_forms),
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
        "I'm making this scene.",
    )
    producer.steps_run("This scene is ready.")

    results = results_of("film")
    assert len(results) == len(quoted_forms) + 1
    assert all("Describe on-screen text instead of quoting it" in result for result in results[:-1])
    sent = handoffs("make_broll_clip")
    assert len(sent) == 1
    assert '"Save today"' not in sent[0]["prompt"]
    assert (
        f"She talks straight to the lens, and says with {voice.rstrip('.')}, "
        f"in a high-energy, excited delivery: <d>[English] {line}</d>" in (sent[0]["prompt"])
    )
    assert "A woman's voice." not in sent[0]["prompt"]
    agent_view = str(handoffs("scene_agent")[0])
    assert "The line takes" not in agent_view
    assert "Clip length:" in agent_view


@pytest.mark.parametrize(
    ("gender", "voice", "said_with"),
    [
        pytest.param(
            "woman",
            "Warm, unhurried, with a soft British accent.",
            "a woman's voice. Warm, unhurried, with a soft British accent",
            id="voice without the gender gets it",
        ),
        pytest.param(
            "man",
            "A calm male voice with an even pace.",
            "A calm male voice with an even pace",
            id="voice that says it is left alone",
        ),
    ],
)
def test_h3_voice_words_always_say_the_plans_gender_once(
    fake_model: FakeModel,
    h3_agent_ready: Job,
    picture_checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    gender: str,
    voice: str,
    said_with: str,
) -> None:
    from jobs import work

    Job.objects.filter(pk=h3_agent_ready.pk).update(person_gender=gender, person_voice=voice)
    line = h3_agent_ready.scenes.get(number=2).line
    fake_model.respond("transcribe_line", {"text": line})
    fake_model.respond(
        "scene_agent",
        turn(calls=[("draw_starting_picture", PICTURE_FORM)]),
        turn(calls=[("film", FILM)]),
        turn(calls=[("give_up", {"reason": "The clip should pass."})]),
    )
    calling(
        fake_model,
        say,
        ("start_scene_agent", {"scene": 2, "instruction": "Film it.", "quotes": ["go on"]}),
    )
    run(fake_model, steps)
    prompt = handoffs("make_broll_clip")[0]["prompt"]
    assert f"Off camera, the presenter says with {said_with}: <d>[English] {line}</d>" in prompt
    # Only the hook, scene 1, asks for a high-energy delivery.
    assert "high-energy" not in prompt
    other = "man's voice" if gender == "woman" else "woman's voice"
    assert other not in prompt.replace(f"a {gender}'s", "")
    clip = ProducedItem.objects.get(kind=ProducedItem.Kind.CLIP)
    assert (clip.sent or {})["voice_words"] == said_with + "."
    assert work.clip_voice_is_current(Scene.objects.get(job=h3_agent_ready, number=2), clip)


def test_a_clip_made_with_the_old_voice_words_stays_current(h3_agent_ready: Job) -> None:
    from jobs import work

    job = h3_agent_ready
    scene = job.scenes.get(number=2)
    clip = ProducedItem.objects.create(
        job=job,
        scene=scene,
        kind=ProducedItem.Kind.CLIP,
        file="clip.mp4",
        sent={"voice_words": f"A {job.person_gender}'s voice. {job.person_voice}"},
    )
    assert work.clip_voice_is_current(scene, clip)
