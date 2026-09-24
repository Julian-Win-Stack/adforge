"""Each scene's line spoken, and what was heard written down, driven through the chat. The
producer's model is faked at the gateway to call the scene tools on an ad whose script has
passed its checks. Both tools work in the background: here the work is held until a test
runs it, so a test can see what the tool handed back before the work is done, and what the
producer was told once it was."""

from collections.abc import Callable
from typing import Any

import pytest
from rest_framework.test import APIClient

from adforge.file_store import read
from adforge.retry import OutsideServiceDown
from agents import tasks
from agents.producer import MakeLineAudio, TranscribeLineAudio
from gateway.fake import FakeModel, meanwhile, turn
from gateway.models import ModelCall
from jobs.models import Job, ProducedItem, Scene, SceneStep

from .conftest import (
    FACTS_OK,
    NO_CHOICES,
    HeldSteps,
    WorkerStopped,
    chat,
    given_to_the_producer,
    paid_for,
    producer_turns,
    results_of,
)

# Each request commits on its own, as on the real server, and so does the producer's work.
pytestmark = pytest.mark.django_db(transaction=True)

LINE = "Meet the Stoneware Mug from Kiln & Co."
# The fake voice says the line's 8 words, "&" among them, at 2 a second.
SECONDS = 4.0

AUDIO_STARTED = "Started scene 1's audio. It isn't made yet: you'll be told when it's ready."
TRANSCRIBING = "Started transcribing scene 1's audio. It isn't done yet: you'll be told when it is."
AUDIO_READY = (
    "Background step finished: scene 1's line's audio is ready (version 1, 4 seconds). It "
    "isn't shown to the shop owner. Transcribe it next. Tell the shop owner."
)
TRANSCRIBED = (
    "Background step finished: scene 1's audio (version 1) was transcribed (version 1). It "
    f'was heard as: "{LINE}" Tell the shop owner.'
)

# Once CHOICE's picture is chosen, the starting picture of scene 1 can be made.
CHOICE: dict[str, Any] = {
    "photo": 1,
    "photo_reason": "The front of the mug shows its glaze, which suits a line that names it.",
    "prompt": "She holds the mug up beside her face, handle out, in her sunny workshop.",
    "prompt_reason": "Holding it up by her face introduces the mug as the line does.",
}


def calling(fake_model: FakeModel, tool: str, reply: str = "On it.", scene: int = 1) -> None:
    """Script the producer to call `tool` for the scene, then reply."""
    fake_model.respond("produce", turn(calls=[(tool, {"scene": scene})]), turn(says=reply))


def made(kind: str) -> list[tuple[int, int]]:
    """Each item of `kind` made, as (scene, version)."""
    return [
        (item.scene.number, item.version)
        for item in Job.objects.get().produced.filter(kind=kind).order_by("id")
        if item.scene is not None
    ]


def told() -> str:
    """The last step result the producer was given, as of its last turn."""
    results: list[str] = [
        each["text"]
        for each in given_to_the_producer(producer_turns())
        if each["kind"] == "step_finished"
    ]
    return results[-1]


@pytest.fixture
def spoken(
    fake_model: FakeModel, checked: None, steps: HeldSteps, say: Callable[..., None]
) -> None:
    """A chat whose scene 1 line has its audio."""
    calling(fake_model, "make_line_audio")
    say("Make scene 1's audio")
    fake_model.respond("produce", turn(says="Scene 1's audio is ready."))
    steps.run_held()


# --- In the background, through the chat ----------------------------------------------------


def test_the_lines_audio_is_made_in_the_background_and_isnt_shown(
    api: APIClient,
    fake_model: FakeModel,
    checked: None,
    steps: HeldSteps,
    session_id: str,
    say: Callable[..., None],
) -> None:
    calling(fake_model, "make_line_audio", reply="I've started scene 1's audio.")
    messages_before = len(chat(api, session_id))

    say("Make scene 1's audio")

    # The tool handed back before the audio was made, and the producer replied.
    assert results_of("make_line_audio") == [AUDIO_STARTED]
    assert made("line_audio") == []
    assert SceneStep.objects.get(kind="line_audio").status == "running"
    assert chat(api, session_id)[-1] == ("agent", "I've started scene 1's audio.")

    fake_model.respond("produce", turn(says="Scene 1's audio is ready!"))
    steps.run_held()

    assert made("line_audio") == [(1, 1)]
    audio = ProducedItem.objects.get(kind="line_audio")
    voice = ProducedItem.objects.get(kind="voice")
    # The line as it stands, spoken in the person's voice.
    assert fake_model.spoken[-1] == LINE
    assert (audio.made_from, audio.seconds) == (voice, SECONDS)
    assert fake_model.heard[read(audio.file)] == LINE
    assert SceneStep.objects.get(kind="line_audio").status == "finished"
    assert told() == AUDIO_READY
    # Nothing was shown: the shop owner sees only the producer's two replies.
    after = api.get(f"/api/sessions/{session_id}/messages/").json()[messages_before:]
    assert [(m["role"], m["text"], m["attachments"]) for m in after] == [
        ("user", "Make scene 1's audio", []),
        ("agent", "I've started scene 1's audio.", []),
        ("agent", "Scene 1's audio is ready!", []),
    ]


def test_the_audio_is_transcribed_in_the_background_word_by_word(
    api: APIClient,
    fake_model: FakeModel,
    spoken: None,
    steps: HeldSteps,
    session_id: str,
    say: Callable[..., None],
) -> None:
    calling(fake_model, "transcribe_line_audio", reply="I'm checking what was heard.")

    say("Transcribe scene 1's audio")

    assert results_of("transcribe_line_audio") == [TRANSCRIBING]
    assert made("transcript") == []

    fake_model.respond("produce", turn(says="It says the line."))
    steps.run_held()

    transcript = ProducedItem.objects.get(kind="transcript")
    audio = ProducedItem.objects.get(kind="line_audio")
    assert transcript.made_from == audio
    assert SceneStep.objects.get(kind="transcript").made_from == audio
    assert transcript.text == LINE
    assert transcript.words[:2] == [
        {"text": "Meet", "start": 0.0, "end": 0.5},
        {"text": "the", "start": 0.5, "end": 1.0},
    ]
    assert len(transcript.words) == 8
    assert told() == TRANSCRIBED
    assert chat(api, session_id)[-1] == ("agent", "It says the line.")


def test_a_whole_scene_is_made_its_picture_and_audio_at_the_same_time(
    api: APIClient,
    fake_model: FakeModel,
    checked: None,
    steps: HeldSteps,
    session_id: str,
    say: Callable[..., None],
) -> None:
    fake_model.respond(
        "produce",
        turn(
            calls=[
                ("make_starting_picture", {"scene": 1, "note": None}),
                ("make_line_audio", {"scene": 1}),
            ]
        ),
        turn(says="I've started scene 1's picture and audio."),
    )
    say("Make scene 1")
    # Both started before either finished.
    assert SceneStep.objects.filter(status="running").count() == 2
    paid_before = len(paid_for())

    fake_model.respond("choose_starting_picture", CHOICE)
    fake_model.respond(
        "produce",
        turn(says="The picture is ready."),
        # Once told the audio is ready, the producer has it transcribed.
        turn(calls=[("transcribe_line_audio", {"scene": 1})]),
        turn(says="The audio is ready, and I'm checking what was heard."),
        turn(says="Scene 1's line was heard as written."),
    )
    steps.run_held()

    assert results_of("transcribe_line_audio") == [TRANSCRIBING]
    assert list(SceneStep.objects.values_list("kind", "status")) == [
        ("starting_picture", "finished"),
        ("line_audio", "finished"),
        ("transcript", "finished"),
    ]
    assert made("starting_picture") == made("line_audio") == made("transcript") == [(1, 1)]
    # Each was paid for once.
    assert paid_for()[paid_before:] == [
        "choose_starting_picture",
        "make_starting_picture",
        "speak_line",
        "transcribe_line",
    ]
    said = [text for role, text in chat(api, session_id) if role == "agent" and text]
    assert said[-3:] == [
        "The picture is ready.",
        "The audio is ready, and I'm checking what was heard.",
        "Scene 1's line was heard as written.",
    ]


def test_audio_finishing_while_the_producer_answers_about_the_picture_isnt_lost(
    fake_model: FakeModel,
    checked: None,
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    fake_model.respond(
        "produce",
        turn(
            calls=[
                ("make_starting_picture", {"scene": 1, "note": None}),
                ("make_line_audio", {"scene": 1}),
            ]
        ),
        turn(says="On it."),
    )
    say("Make scene 1")
    before = producer_turns()

    fake_model.respond("choose_starting_picture", CHOICE)
    fake_model.respond(
        "produce",
        # The audio is made while the producer works out what to say about the picture.
        meanwhile(steps.run_next, turn(says="The picture is ready!")),
        turn(says="The audio is ready too!"),
    )
    steps.run_next()

    assert producer_turns() == before + 2
    first, second = given_to_the_producer(before + 1), given_to_the_producer(before + 2)
    assert first[-1]["text"].startswith("Background step finished: scene 1's starting picture")
    # The audio's result comes after the reply that hadn't seen it.
    assert second[-2] == {"kind": "said", "by": "agent", "text": "The picture is ready!"}
    assert second[-1] == {"kind": "step_finished", "text": AUDIO_READY}
    assert not SceneStep.objects.filter(producer_read_at=None).exists()


def test_audio_isnt_transcribed_before_it_is_made(
    fake_model: FakeModel, checked: None, steps: HeldSteps, say: Callable[..., None]
) -> None:
    fake_model.respond(
        "produce",
        turn(calls=[("transcribe_line_audio", {"scene": 1})]),
        turn(calls=[("make_line_audio", {"scene": 1})]),
        turn(calls=[("transcribe_line_audio", {"scene": 1})]),
        turn(says="I'll transcribe it once it's ready."),
    )

    say("Make scene 1's audio and transcribe it")

    assert results_of("transcribe_line_audio") == [
        "Refused: scene 1 has no audio yet. Make the line's audio first. Nothing was done.",
        "Refused: scene 1's audio is still being made. You'll be told when it's ready; "
        "transcribe it then. Nothing was done.",
    ]
    assert not SceneStep.objects.filter(kind="transcript").exists()
    assert "transcribe_line" not in paid_for()


def test_audio_that_cant_be_transcribed_fails_its_step_and_the_producer_is_told(
    api: APIClient,
    fake_model: FakeModel,
    spoken: None,
    steps: HeldSteps,
    session_id: str,
    say: Callable[..., None],
) -> None:
    calling(fake_model, "transcribe_line_audio")
    say("Transcribe scene 1's audio")
    fake_model.respond("transcribe_line", *[OutsideServiceDown("503 from ElevenLabs")] * 3)
    fake_model.respond("produce", turn(says="Sorry, the transcription service is down."))

    steps.run_held()

    step = SceneStep.objects.get(kind="transcript")
    assert step.status == "failed"
    assert step.reason.startswith("an outside service stayed down after several tries")
    assert made("transcript") == []
    assert told() == (
        "Background step failed: scene 1's transcript couldn't be made: "
        f"{step.reason} Tell the shop owner what went wrong."
    )
    # Every try was recorded, against the tool call that started the step.
    tries = ModelCall.objects.filter(purpose="transcribe_line")
    assert [(t.attempt, t.outcome) for t in tries] == [(1, "failed"), (2, "failed"), (3, "failed")]
    assert {t.tool_call_id for t in tries} == {step.tool_call_id}
    assert chat(api, session_id)[-1] == ("agent", "Sorry, the transcription service is down.")


def test_a_line_that_cant_be_spoken_fails_its_step_and_the_producer_is_told(
    fake_model: FakeModel, checked: None, steps: HeldSteps, say: Callable[..., None]
) -> None:
    calling(fake_model, "make_line_audio")
    say("Make scene 1's audio")
    fake_model.respond("speak_line", *[OutsideServiceDown("503 from Inworld")] * 3)
    fake_model.respond("produce", turn(says="Sorry, the voice service is down."))

    steps.run_held()

    step = SceneStep.objects.get(kind="line_audio")
    assert step.status == "failed"
    assert made("line_audio") == []
    assert told() == (
        "Background step failed: scene 1's line's audio couldn't be made: "
        f"{step.reason} Tell the shop owner what went wrong."
    )
    assert ModelCall.objects.filter(purpose="speak_line", outcome="failed").count() == 3


# --- What the tools refuse ------------------------------------------------------------------


@pytest.fixture
def unchecked(fake_model: FakeModel, planned: None, say: Callable[..., None]) -> None:
    """A chat whose ad has its person, but whose script hasn't been checked."""
    fake_model.respond("produce", turn(calls=[("create_person", {})]), turn(says="Meet her!"))
    say("Make the person")


@pytest.mark.parametrize("tool", ["make_line_audio", "transcribe_line_audio"])
def test_a_line_that_hasnt_passed_the_fact_check_gets_no_audio_or_transcript(
    fake_model: FakeModel,
    unchecked: None,
    steps: HeldSteps,
    say: Callable[..., None],
    tool: str,
) -> None:
    paid = paid_for()
    calling(fake_model, tool)

    say("Make scene 1's audio")

    assert results_of(tool) == [
        "Refused: scene 1's line hasn't passed the fact check, and nothing is made for a line "
        "until it has. Run the planning checks first. Nothing was done."
    ]
    assert not SceneStep.objects.exists()
    assert steps.held == []
    assert paid_for() == paid


def test_a_line_gets_no_audio_before_the_person_is_made(
    fake_model: FakeModel, planned: None, steps: HeldSteps, say: Callable[..., None]
) -> None:
    fake_model.respond(
        "produce",
        turn(calls=[("run_planning_checks", NO_CHOICES)]),
        turn(calls=[("make_line_audio", {"scene": 1})]),
        turn(says="I need to make the person first."),
    )
    fake_model.respond("fact_check", FACTS_OK)

    say("Check the script and make scene 1's audio")

    assert results_of("make_line_audio") == [
        "Refused: the person hasn't been made yet, and the audio is in their voice. Create the "
        "person first. Nothing was done."
    ]
    assert not SceneStep.objects.exists()


def test_audio_already_being_made_isnt_started_again(
    fake_model: FakeModel, checked: None, steps: HeldSteps, say: Callable[..., None]
) -> None:
    fake_model.respond(
        "produce",
        turn(calls=[("make_line_audio", {"scene": 1})]),
        turn(calls=[("make_line_audio", {"scene": 1})]),
        turn(says="On it."),
    )

    say("Make scene 1's audio")

    assert results_of("make_line_audio") == [
        AUDIO_STARTED,
        "Refused: scene 1's audio is already being made. You'll be told when it's ready. "
        "Nothing was done.",
    ]
    assert SceneStep.objects.count() == 1


def test_the_tools_take_no_text_so_only_the_line_as_it_stands_is_spoken() -> None:
    assert list(MakeLineAudio.model_json_schema()["properties"]) == ["scene"]
    assert list(TranscribeLineAudio.model_json_schema()["properties"]) == ["scene"]


# --- Versions -------------------------------------------------------------------------------


def test_a_changed_line_gets_new_audio_and_the_old_audio_is_kept(
    fake_model: FakeModel, spoken: None, steps: HeldSteps, say: Callable[..., None]
) -> None:
    first = ProducedItem.objects.get(kind="line_audio")
    first_audio = read(first.file)
    Scene.objects.filter(number=1).update(line="Say hello to the Stoneware Mug.")
    calling(fake_model, "make_line_audio")
    say("Make scene 1's audio again")
    fake_model.respond("produce", turn(says="Here's the new audio."))
    steps.run_held()

    assert made("line_audio") == [(1, 1), (1, 2)]
    assert read(first.file) == first_audio
    assert fake_model.spoken[-2:] == [LINE, "Say hello to the Stoneware Mug."]
    assert paid_for().count("speak_line") == 2
    assert told().startswith(
        "Background step finished: scene 1's line's audio is ready (version 2, 3 seconds)."
    )


def test_audio_for_an_earlier_line_isnt_transcribed(
    fake_model: FakeModel, spoken: None, steps: HeldSteps, say: Callable[..., None]
) -> None:
    Scene.objects.filter(number=1).update(line="Say hello to the Stoneware Mug.")
    calling(fake_model, "transcribe_line_audio")

    say("Transcribe scene 1's audio")

    assert results_of("transcribe_line_audio") == [
        "Refused: scene 1's audio was made for an earlier line, and the line has changed "
        "since. Make the line's audio again first. Nothing was done."
    ]
    assert "transcribe_line" not in paid_for()


def test_a_transcript_is_kept_exactly_as_heard_even_when_the_line_was_misspoken(
    fake_model: FakeModel, spoken: None, steps: HeldSteps, say: Callable[..., None]
) -> None:
    calling(fake_model, "transcribe_line_audio")
    say("Transcribe scene 1's audio")
    fake_model.respond("transcribe_line", {"text": "Meet the the Stoneware Mug from Kiln & Co."})
    fake_model.respond("produce", turn(says="It was misspoken."))
    steps.run_held()

    transcript = ProducedItem.objects.get(kind="transcript")
    assert transcript.text == "Meet the the Stoneware Mug from Kiln & Co."
    assert [word["text"] for word in transcript.words] == [
        "Meet",
        "the",
        "the",
        "Stoneware",
        "Mug",
        "from",
        "Kiln",
        "&",
        "Co.",
    ]


# --- Nothing is paid for twice --------------------------------------------------------------


def test_audio_already_made_for_the_line_is_handed_back_and_charges_nothing(
    fake_model: FakeModel, spoken: None, steps: HeldSteps, say: Callable[..., None]
) -> None:
    paid = paid_for()
    calling(fake_model, "make_line_audio")

    say("Make scene 1's audio")

    assert paid_for() == paid
    assert steps.held == []
    assert SceneStep.objects.count() == 1
    # 38 characters at $25 a million.
    assert results_of("make_line_audio")[-1] == (
        "Scene 1's audio was already made for this line in this voice (version 1), so nothing "
        "was made or paid for again. Making it cost $0.00095."
    )


def test_audio_already_transcribed_is_handed_back_and_charges_nothing(
    fake_model: FakeModel, spoken: None, steps: HeldSteps, say: Callable[..., None]
) -> None:
    calling(fake_model, "transcribe_line_audio")
    say("Transcribe scene 1's audio")
    fake_model.respond("produce", turn(says="Done."))
    steps.run_held()
    paid = paid_for()
    calling(fake_model, "transcribe_line_audio")

    say("Transcribe scene 1's audio")

    assert paid_for() == paid
    assert steps.held == []
    # 4 seconds at $0.22 an hour.
    assert results_of("transcribe_line_audio")[-1] == (
        "Scene 1's audio (version 1) was already transcribed (version 1), so nothing was made "
        f'or paid for again. Transcribing it cost $0.000244. It was heard as: "{LINE}"'
    )


@pytest.mark.parametrize(
    ("tool", "purpose", "kind"),
    [
        ("make_line_audio", "speak_line", "line_audio"),
        ("transcribe_line_audio", "transcribe_line", "transcript"),
    ],
)
def test_work_paid_for_before_the_worker_stopped_is_kept_rather_than_paid_for_again(
    fake_model: FakeModel,
    spoken: None,
    steps: HeldSteps,
    say: Callable[..., None],
    monkeypatch: pytest.MonkeyPatch,
    tool: str,
    purpose: str,
    kind: str,
) -> None:
    if kind == "line_audio":
        # A new line, so the audio is made again.
        Scene.objects.filter(number=1).update(line="Say hello to the Stoneware Mug.")
    calling(fake_model, tool)
    say("Go on")
    (step_id,) = steps.held
    paid = paid_for().count(purpose)

    def the_worker_stops(*_: object, **__: object) -> None:
        raise WorkerStopped

    # The worker stops once the work is paid for, as it is being kept.
    with monkeypatch.context() as stopping:
        stopping.setattr(ProducedItem.objects, "create", the_worker_stops)
        with pytest.raises(WorkerStopped):
            steps.run_next()
    fake_model.respond("produce", turn(says="Ready!"))
    tasks.run_scene_step(step_id)

    assert paid_for().count(purpose) == paid + 1
    assert SceneStep.objects.get(pk=step_id).status == "finished"
    assert len(ProducedItem.objects.filter(kind=kind, step_id=step_id)) == 1


def test_audio_in_an_earlier_voice_isnt_transcribed(
    fake_model: FakeModel, spoken: None, steps: HeldSteps, say: Callable[..., None]
) -> None:
    # The person was made again: a new voice, so the old audio is no longer theirs.
    voice = ProducedItem.objects.get(kind="voice")
    ProducedItem.objects.create(
        job=voice.job, kind="voice", version=2, voice_id="fake-voice-2", words_per_second=2.0
    )
    calling(fake_model, "transcribe_line_audio")

    say("Transcribe scene 1's audio")

    assert results_of("transcribe_line_audio") == [
        "Refused: scene 1's audio was made in an earlier voice, and the person has changed "
        "since. Make the line's audio again first. Nothing was done."
    ]
    assert "transcribe_line" not in paid_for()
