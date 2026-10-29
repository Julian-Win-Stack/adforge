"""The shop owner's answer to the fixing agent's question (ticket #245, spec #210 point 23):
code hands it in through `owner_answers.answer` (the producer will, #260/#261). A photo
enters the library labelled as an owner photo, a video is watched and its look frames enter
the library, and the words are the answer's text. The parked fix's step runs again in a
worker; the agent reads the answer and the new item numbers as its ask_owner call's result,
and goes on; a try that passes replaces the scene's clip and the ad is assembled again. The
agent, the judge, the labelling call, the watcher and the video API are faked at the
gateway."""

from collections.abc import Callable
from typing import Any

import pytest
from rest_framework.test import APIClient

from adforge import file_store
from agents import fixing_agent, tasks
from agents.models import ToolCall
from chat.models import Message
from gateway.fake import FakeModel, turn
from gateway.models import ModelCall
from gateway.types import StepFinished, ToolUse
from jobs import owner_answers, work
from jobs.library import LookFrame, OwnerPhoto, library_of, sent_list_of
from jobs.models import AgentFix, Job, ProducedItem, ProductVideo

from . import fixing
from .conftest import LABELLED, NOTHING_WATCHED, HeldSteps, WorkerStopped, picture
from .sent_lists import piece
from .test_fixing_agent import POURS, SLOWER, failing_first, film_again
from .test_fixing_agent_ask_owner import QUESTION, ask
from .test_producer_auto_fix import FLOATS, REMADE, failing, rewrite
from .test_producer_broll import (  # noqa: F401 (a fixture)
    assemble_through_the_chat,
    calling,
    checked,
    run,
)
from .test_recolour import RECOLOUR_OK

pytestmark = pytest.mark.django_db(transaction=True)

WORDS = "No video, sorry: here is a photo of the lid screwed on."
PROMPT = "A hand screws the lid onto the mug and lets go."


def parked(fake_model: FakeModel, steps: HeldSteps, say: Callable[..., None]) -> ProducedItem:
    """Scene 2's clip and its first try fail the judge, the agent asks the shop owner for a
    video, and the ad is assembled with the last try. Gives the ad."""
    fake_model.respond("check_clip_motion", *[failing(FLOATS)] * 2)
    fake_model.respond(
        "fix_scene", turn(calls=[film_again(video_prompt=POURS)]), turn(calls=[ask()])
    )
    return assemble_through_the_chat(fake_model, steps, say)


def labelling_calls() -> int:
    return ModelCall.objects.filter(purpose="label_photo").count()


def resumed_turn(fake_model: FakeModel) -> ToolUse:
    """The ask_owner call as the agent read it on the turn the fix resumed."""
    turns = [each for each in fake_model.turns_taken if each.purpose == "fix_scene"]
    [ask_read] = [
        each
        for each in turns[2].handoff.conversation
        if isinstance(each, ToolUse) and each.tool == "ask_owner"
    ]
    return ask_read


def test_a_photo_and_words_enter_the_library_and_the_fix_films_with_them_into_the_ad(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    api: APIClient,
    session_id: str,
) -> None:
    first_ad = parked(fake_model, steps, say)
    job = Job.objects.get()
    question = work.open_question(job.scenes.get(number=2))
    assert question is not None and question.question == QUESTION
    photo = file_store.save("chat/lid-on.png", picture(40, 30, (20, 120, 40)))
    number = len(library_of(job)) + 1
    references = {"pictures": [{"number": number, "used_for": "state"}], "piece": None}
    fake_model.respond(
        "fix_scene", turn(calls=[film_again(references=references, video_prompt=PROMPT)])
    )
    # The photo's colour isn't the clip's, so it is recoloured to it before it is sent.
    fake_model.respond("check_recolour", RECOLOUR_OK)

    turns_before = len([each for each in fake_model.turns_taken if each.purpose == "produce"])

    owner_answers.answer(question, words=WORDS, photos=[photo])
    run(fake_model, steps)

    # The photo is the library's next item, as the shop owner's photo.
    [item] = [each for each in library_of(job) if each.number == number]
    assert (item.file, item.source) == (photo, OwnerPhoto(original=photo))
    # The agent read the words and the new item as its ask's result, with the item's picture.
    ask_read = resumed_turn(fake_model)
    assert WORDS in ask_read.result and f"library item {number}" in ask_read.result
    assert [picture.key for picture in ask_read.pictures] == [photo]
    # It filmed again naming the item; that try passed, and is scene 2's clip in a new ad.
    fixing = AgentFix.objects.get()
    passed = fixing.tries.get(number=2).clip
    assert fixing.passed == passed and passed is not None
    sent = sent_list_of(passed)
    assert sent is not None
    assert number in [each.library_number for each in sent.pictures] + [
        each.of for each in sent.recolours
    ]
    ads = job.produced.filter(kind=ProducedItem.Kind.FINISHED_AD).order_by("version")
    assert [ad.pk for ad in ads][0] == first_ad.pk and ads.count() == 2
    assert {cut["scene"]: cut["clip"] for cut in ads.last().cuts}[2] == passed.pk  # type: ignore[union-attr]
    # The producer is woken and told the step's new result, as for any finished step.
    produced = [each for each in fake_model.turns_taken if each.purpose == "produce"]
    assert len(produced) == turns_before + 1
    told = [
        each.text for each in produced[-1].handoff.conversation if isinstance(each, StepFinished)
    ]
    assert "once the shop owner answered its question, the judge passed try 2." in told[-1]
    # The scene's card no longer shows the question.
    shown = api.get(f"/api/sessions/{session_id}/messages/").json()
    assert all(not each["question"]["open"] for each in shown if each["question"])
    assert ToolCall.objects.get(tool="ask_owner").finished_at is not None
    assert Message.objects.filter(owner_question=question, step_state="started").exists()


def test_an_answer_of_words_only_resumes_the_fix_with_the_words_and_no_item(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    parked(fake_model, steps, say)
    job = Job.objects.get()
    question = work.open_question(job.scenes.get(number=2))
    assert question is not None
    in_the_library = len(library_of(job))
    fake_model.respond("fix_scene", turn(calls=[film_again()]))

    owner_answers.answer(question, words="It screws on a quarter turn, clockwise.")
    # The question takes one answer.
    with pytest.raises(owner_answers.NotWaiting):
        owner_answers.answer(question, words="Half a turn.")
    run(fake_model, steps)

    ask_read = resumed_turn(fake_model)
    assert ask_read.result.splitlines()[:2] == [
        'The shop owner answered: "It screws on a quarter turn, clockwise."',
        "The shop owner sent no file.",
    ]
    assert ask_read.pictures == []
    assert len(library_of(job)) == in_the_library
    fixing = AgentFix.objects.get()
    assert fixing.passed == fixing.tries.get(number=2).clip


def test_a_video_is_watched_and_its_moments_and_look_frames_reach_the_agent(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    parked(fake_model, steps, say)
    job = Job.objects.get()
    question = work.open_question(job.scenes.get(number=2))
    assert question is not None
    video = file_store.save("chat/lid.mp4", piece(3.0))
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
    number = len(library_of(job)) + 1
    fake_model.respond("fix_scene", turn(calls=[film_again()]))

    # A page video whose watch failed on the page read isn't watched with the answer.
    ProductVideo.objects.create(
        job=job,
        position=9,
        kind=ProductVideo.Kind.VIDEO,
        file=file_store.save("page_video_9.mp4", piece(2.0)),
        duration=2.0,
    )
    watched_before = len(fake_model.videos_watched)

    owner_answers.answer(question, words="", videos=[video])
    run(fake_model, steps)

    [kept] = ProductVideo.objects.filter(file=video)
    assert len(fake_model.videos_watched) == watched_before + 1
    [frame] = [each for each in library_of(job) if each.number == number]
    assert frame.source == LookFrame(video=kept.position, second=1.0)
    ask_read = resumed_turn(fake_model)
    assert ask_read.result.splitlines()[:2] == [
        "The shop owner wrote no words.",
        f"Their video is page video {kept.position}, watched: moments 1 to 1. Its look frames "
        f"are library items {number}.",
    ]
    assert [picture.key for picture in ask_read.pictures] == [frame.file]


def test_a_worker_restart_while_taking_the_answer_runs_the_step_again_adding_the_photo_once(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The agent asks before filming any try, so the scene's first clip is in the ad.
    fake_model.respond("check_clip_motion", failing(FLOATS))
    fake_model.respond("fix_scene", turn(calls=[ask()]))
    assemble_through_the_chat(fake_model, steps, say)
    job = Job.objects.get()
    question = work.open_question(job.scenes.get(number=2))
    assert question is not None
    photo = file_store.save("chat/lid-on.png", picture(40, 30, (20, 120, 40)))
    in_the_library = len(library_of(job))
    labelled_before = labelling_calls()
    resume = fixing_agent.answer_question

    def stops_once(*args: Any) -> Any:
        monkeypatch.setattr(fixing_agent, "answer_question", resume)
        raise WorkerStopped()

    monkeypatch.setattr(fixing_agent, "answer_question", stops_once)
    step = owner_answers.answer(question, words=WORDS, photos=[photo])
    with pytest.raises(WorkerStopped):
        steps.run_held()

    tasks.carry_on_after_the_worker_starts()

    # The step is queued to run again, not failed: its scene stays finished, the answer kept.
    step.refresh_from_db()
    assert (step.status, steps.held) == ("running", [step.pk])
    assert job.scenes.get(number=2).status == "finished"
    question.refresh_from_db()
    assert question.answer is not None
    assert not Message.objects.filter(step_scene=2, step_state="not_made").exists()

    fake_model.respond("fix_scene", turn(calls=[film_again()]))
    run(fake_model, steps)

    fixing = AgentFix.objects.get()
    assert fixing.passed == fixing.tries.get(number=1).clip
    assert len(library_of(job)) == in_the_library + 1
    assert labelling_calls() == labelled_before + 1
    notices = Message.objects.filter(text__startswith="What you sent is in the ad's picture")
    assert notices.count() == 1


def test_an_error_after_the_answer_keeps_the_last_try_in_the_ad(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    ad = parked(fake_model, steps, say)
    job = Job.objects.get()
    last_try = AgentFix.objects.get().tries.get(number=1).clip
    assert last_try is not None
    question = work.open_question(job.scenes.get(number=2))
    assert question is not None
    fake_model.respond("fix_scene", RuntimeError("The agent's model broke."))

    owner_answers.answer(question, words="A quarter turn.")
    run(fake_model, steps)

    # The ad keeps the last try, never the first clip the judge failed.
    ads = job.produced.filter(kind=ProducedItem.Kind.FINISHED_AD).order_by("version")
    shown = ads.last()
    assert shown is not None and shown.pk == ad.pk
    newest = job.scenes.get(number=2).produced.filter(kind="clip").order_by("id").last()
    assert newest is not None and newest.file == last_try.file
    # The shop owner is shown every try, as for a give-up.
    assert Message.objects.filter(tries_of=AgentFix.objects.get().step).exists()


def test_a_change_to_the_scene_withdraws_the_question_and_an_answer_is_refused(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    parked(fake_model, steps, say)
    job = Job.objects.get()
    question = work.open_question(job.scenes.get(number=2))
    assert question is not None

    # The shop owner's note plans a new clip of scene 2, which the judge passes.
    fixing.fixed(2, "new_clip", change=SLOWER, note=SLOWER)
    fake_model.respond("rewrite_clip_prompt", rewrite(REMADE))
    steps.run_held()

    assert work.open_question(job.scenes.get(number=2)) is None
    assert Message.objects.filter(
        text__startswith="The fixing agent's question about scene 2's clip is withdrawn"
    ).exists()
    with pytest.raises(owner_answers.NotWaiting):
        owner_answers.answer(question, words="A quarter turn.")


def test_an_answer_to_a_question_the_scene_moved_past_is_refused(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parked(fake_model, steps, say)
    job = Job.objects.get()
    question = work.open_question(job.scenes.get(number=2))
    assert question is not None
    # A change that leaves the question open: the answer is still refused.
    monkeypatch.setattr(owner_answers, "withdraw", lambda scene: None)
    fixing.fixed(2, "new_clip", change=SLOWER, note=SLOWER)
    fake_model.respond("rewrite_clip_prompt", rewrite(REMADE))
    steps.run_held()
    assert work.open_question(job.scenes.get(number=2)) is not None

    with pytest.raises(owner_answers.NotWaiting, match="newer version"):
        owner_answers.answer(question, words="A quarter turn.")
    assert not steps.held


def test_a_second_fix_on_the_scene_after_the_resumed_one_works_as_before(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    parked(fake_model, steps, say)
    job = Job.objects.get()
    question = work.open_question(job.scenes.get(number=2))
    assert question is not None
    fake_model.respond("fix_scene", turn(calls=[film_again()]))
    owner_answers.answer(question, words="A quarter turn.")
    run(fake_model, steps)
    resumed = AgentFix.objects.get()

    # The shop owner's note plans a new clip of scene 2; the judge fails it, and the fixing
    # agent films it again, which the judge passes and the ad gets.
    fixing.fixed(2, "new_clip", change=SLOWER, note=SLOWER)
    fake_model.respond("rewrite_clip_prompt", rewrite(REMADE))
    fake_model.answer_unscripted("check_clip_motion", failing_first(1))
    fake_model.respond("fix_scene", turn(calls=[film_again(video_prompt=POURS)]))
    steps.run_held()

    second = AgentFix.objects.exclude(pk=resumed.pk).get()
    assert second.step != resumed.step and second.passed is not None
    ads = job.produced.filter(kind=ProducedItem.Kind.FINISHED_AD).order_by("version")
    assert {cut["scene"]: cut["clip"] for cut in ads.last().cuts}[2] == second.passed.pk  # type: ignore[union-attr]
    # Its first turn holds nothing of the fix before it.
    turns = [each for each in fake_model.turns_taken if each.purpose == "fix_scene"]
    assert len(turns) == 4
    assert [each.tool for each in turns[3].handoff.conversation if isinstance(each, ToolUse)] == [
        "look_at_try"
    ]
    assert work.open_question(job.scenes.get(number=2)) is None


def test_a_resumed_step_the_worker_stops_in_twice_fails_and_keeps_its_clip_in_the_ad(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The agent asks before filming any try, so the scene's first clip is in the ad.
    fake_model.respond("check_clip_motion", failing(FLOATS))
    fake_model.respond("fix_scene", turn(calls=[ask()]))
    ad = assemble_through_the_chat(fake_model, steps, say)
    job = Job.objects.get()
    in_the_ad = {cut["scene"]: cut["clip"] for cut in ad.cuts}[2]
    question = work.open_question(job.scenes.get(number=2))
    assert question is not None

    def stops(*args: Any) -> Any:
        raise WorkerStopped()

    # The resume stops the worker every time.
    monkeypatch.setattr(fixing_agent, "answer_question", stops)
    step = owner_answers.answer(question, words=WORDS)
    with pytest.raises(WorkerStopped):
        steps.run_held()
    tasks.carry_on_after_the_worker_starts()
    with pytest.raises(WorkerStopped):
        steps.run_held()
    fake_model.respond("produce", turn(says="Done."))

    tasks.carry_on_after_the_worker_starts()

    # Queued again once only: the second time it fails, said in red and kept in the warnings.
    step.refresh_from_db()
    assert (step.status, steps.held) == ("failed", [])
    said = (
        "Scene 2's fix couldn't carry on with your answer: its work stopped partway again "
        "after it was started again, so the clip that was in the ad stays there."
    )
    assert Message.objects.filter(text=said, level="problem").count() == 1
    assert said in [each["text"] for each in Job.objects.get().warnings]
    # The scene keeps its clip in the ad, and isn't planned again.
    scene = job.scenes.get(number=2)
    assert scene.status == "finished"
    newest = scene.produced.filter(kind="clip").order_by("id").last()
    first = ProducedItem.objects.get(pk=in_the_ad)
    assert newest is not None and newest.file == first.file
