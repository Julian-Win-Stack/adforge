"""Owner media enters the current scene chat through the checked producer note."""

import json
from collections.abc import Callable

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIClient

from adforge.retry import OutsideServiceDown
from agents import tasks
from gateway.fake import FakeModel, turn
from gateway.models import ModelCall
from jobs import owner_answers
from jobs.library import LookFrame, OwnerPhoto, library_of
from jobs.models import Job, ProductVideo, SceneStep
from jobs.reference_photos import clean_photo_of

from .conftest import (
    LABELLED,
    NOTHING_WATCHED,
    HeldSteps,
    WorkerStopped,
    handoffs,
    picture,
    recorded,
)
from .legacy_records import historical_question
from .sent_lists import piece
from .test_producer_broll import checked  # noqa: F401
from .test_product_only import failing as cut_out_failing
from .test_scene_agent import scene_agent_ready  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)
WORDS = "Here is the missing view."


def send_photo(fake_model: FakeModel, say: Callable[..., None]) -> None:
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
                                "instruction": "Use the owner's missing view.",
                                "quotes": [WORDS],
                            }
                        ]
                    },
                )
            ]
        ),
        turn(says="Thanks."),
    )
    say(WORDS, ("view.png", picture(40, 30, (20, 120, 40))))


def finish(fake_model: FakeModel, steps: HeldSteps) -> None:
    fake_model.respond("scene_agent", turn(calls=[("give_up", {"reason": "Need another view."})]))
    fake_model.respond("produce", turn(says="The agent has your answer."))
    steps.run_held()


def test_owner_photo_is_cleaned_for_the_question_and_reaches_the_scene_chat_once(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    session_id: str,
) -> None:
    question = historical_question(session_id)
    job = Job.objects.get()
    number = len(library_of(job)) + 1
    send_photo(fake_model, say)
    finish(fake_model, steps)
    [item] = [each for each in library_of(job) if each.number == number]
    assert isinstance(item.source, OwnerPhoto)
    [drawn] = ModelCall.objects.filter(purpose="make_reference_photo_picture")
    assert drawn.handoff["pictures"] == [item.source.original]
    assert drawn.handoff["prompt"] == clean_photo_of(
        f"the thing the photo shows that answers this question: {question.question}"
    )
    assert drawn.output == {"file": item.file}
    given = json.dumps(handoffs("scene_agent")[-1])
    assert WORDS in given and f"Their photo is library item {number}" in given
    question.refresh_from_db()
    assert question.answered_at is not None
    assert not question.agent_fix.is_scene_agent
    assert not handoffs("fix_scene")
    tasks.run_scene_step(SceneStep.objects.get(kind="scene_agent").pk)
    assert ModelCall.objects.filter(purpose="make_reference_photo_picture").count() == 1


def test_words_alone_close_the_old_question_without_opening_a_worker_or_changing_the_library(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    steps: HeldSteps,
    session_id: str,
) -> None:
    question = historical_question(session_id)
    before = library_of(Job.objects.get())
    assert owner_answers.answer(question, words="I don't have that view.").kind == "nothing"
    with pytest.raises(owner_answers.NotWaiting):
        owner_answers.answer(question, words="Another answer.")
    assert not steps.held and not handoffs("scene_agent")
    assert library_of(Job.objects.get()) == before
    question.refresh_from_db()
    assert question.answered_at is not None


def test_a_photo_that_cannot_be_cleaned_is_reported_and_the_scene_continues_without_it(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    session_id: str,
    api: APIClient,
) -> None:
    historical_question(session_id)
    before = len(library_of(Job.objects.get()))
    fake_model.respond(
        "check_cut_out",
        *(
            cut_out_failing(problem)
            for problem in (
                "A hand is still in it.",
                "The lid is changed.",
                "The handle is missing.",
            )
        ),
    )
    send_photo(fake_model, say)
    finish(fake_model, steps)
    assert len(library_of(Job.objects.get())) == before
    assert "Their photo isn't in the library: all 3 tries failed" in json.dumps(
        handoffs("scene_agent")[-1]
    )
    assert any("I couldn't use a photo you sent" in text for _, text in recorded(api, session_id))


def test_an_unavailable_clean_picture_check_keeps_the_photo_unchecked_and_records_the_problem(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    session_id: str,
    api: APIClient,
) -> None:
    question = historical_question(session_id)
    before = len(library_of(Job.objects.get()))
    fake_model.respond(
        "check_cut_out", *[OutsideServiceDown("the vision service answered 503")] * 3
    )
    send_photo(fake_model, say)
    finish(fake_model, steps)
    assert len(library_of(Job.objects.get())) == before + 1
    said = (
        "Checking the clean picture of the thing the photo shows that answers this question: "
        f"{question.question} gave no answer (the AI service checking it didn't answer), so it "
        "is kept unchecked."
    )
    assert ("problem", said) in recorded(api, session_id)


def test_only_the_answer_video_is_watched_and_its_moments_and_look_frames_reach_the_scene(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    session_id: str,
    api: APIClient,
) -> None:
    historical_question(session_id)
    job = Job.objects.get()
    from adforge import file_store

    ProductVideo.objects.create(
        job=job,
        position=9,
        kind="video",
        file=file_store.save("unwatched.mp4", piece(2.0)),
        duration=2.0,
    )
    number = len(library_of(job)) + 1
    fake_model.respond(
        "watch_video",
        {
            **NOTHING_WATCHED,
            "moments": [
                {
                    "start": 0.0,
                    "end": 2.5,
                    "happens": "The lid is screwed on.",
                    "on_screen": "A mug.",
                }
            ],
            "look_frames": [{**LABELLED, "kind": "state", "second": 1.0}],
        },
    )
    fake_model.respond(
        "produce",
        turn(
            calls=[
                (
                    "pass_note",
                    {
                        "scenes": [
                            {"scene": 2, "instruction": "Use the missing view.", "quotes": [WORDS]}
                        ]
                    },
                )
            ]
        ),
        turn(says="Thanks."),
    )
    watched = len(fake_model.videos_watched)
    sent = api.post(
        f"/api/sessions/{session_id}/messages/",
        {"text": WORDS, "videos": [SimpleUploadedFile("answer.mp4", piece(3.0), "video/mp4")]},
        format="multipart",
    )
    assert sent.status_code == 201, sent.json()
    finish(fake_model, steps)
    assert len(fake_model.videos_watched) == watched + 1
    [frame] = [each for each in library_of(job) if each.number == number]
    assert isinstance(frame.source, LookFrame) and frame.source.second == 1.0
    given = json.dumps(handoffs("scene_agent")[-1])
    assert "watched: moments 1 to 1" in given and f"library items {number}" in given


def test_a_worker_restart_after_receiving_the_photo_does_not_clean_or_label_it_twice(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    session_id: str,
) -> None:
    question = historical_question(session_id)
    before = len(library_of(Job.objects.get()))
    send_photo(fake_model, say)
    fake_model.respond("scene_agent", WorkerStopped())
    with pytest.raises(WorkerStopped):
        steps.run_held()
    tasks.carry_on_after_the_worker_starts()
    finish(fake_model, steps)
    assert len(library_of(Job.objects.get())) == before + 1
    assert ModelCall.objects.filter(purpose="make_reference_photo_picture").count() == 1
    question.refresh_from_db()
    assert question.answered_at is not None
    assert "Their photo is library item" in json.dumps(handoffs("scene_agent")[-1])


@pytest.mark.parametrize("ending_turn", [False, True])
def test_a_photo_arriving_during_the_scene_turn_is_read_even_when_that_turn_ends_the_run(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    ending_turn: bool,
) -> None:
    from gateway.fake import meanwhile

    from .test_producer_broll import calling
    from .test_scene_agent_questions import ASK

    def answer_while_the_model_is_working() -> None:
        send_photo(fake_model, say)

    action = (
        ("give_up", {"reason": "This turn ended before the photo arrived."})
        if ending_turn
        else ("look_at_library", {"item": 1, "video": None, "moment": None})
    )
    fake_model.respond(
        "scene_agent",
        turn(calls=[ASK]),
        meanwhile(answer_while_the_model_is_working, turn(calls=[action])),
        turn(calls=[("give_up", {"reason": "The answer still misses the needed view."})]),
    )
    calling(
        fake_model,
        say,
        ("start_scene_agent", {"scene": 2, "instruction": "Show the base.", "quotes": ["Go on"]}),
    )
    fake_model.respond("produce", turn(says="The scene has your answer."))
    steps.run_held()
    # A late answer reaches a fresh run; an earlier answer reaches the next turn of this run.
    assert "Their photo is library item" in json.dumps(handoffs("scene_agent")[-1])
    assert WORDS in json.dumps(handoffs("scene_agent")[-1])
    assert ModelCall.objects.filter(purpose="make_reference_photo_picture").count() == 1
    from jobs.models import OwnerQuestion

    assert OwnerQuestion.objects.get().answered_at is not None
