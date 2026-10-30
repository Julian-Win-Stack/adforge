"""The shop owner's answer to the fixing agent's question (ticket #245, spec #210 point 23,
as the round 7 spec's section 4 changed them): code hands it in through
`owner_answers.answer` (the producer's fix_scene does). A photo enters the library labelled
as an owner photo, a video is watched and its look frames enter the library. A fix still
running is told of it before its next turn; a fix that ended with no try passing starts
again from it in its step run again in a worker, and a try that passes replaces the scene's
clip and the ad is assembled again; a scene whose clip passed only gets it in the library.
Words alone, a "no", change nothing. The agent, the judge, the labelling call, the watcher
and the video API are faked at the gateway."""

from collections.abc import Callable
from typing import Any

import pytest
from django.db import transaction
from rest_framework.test import APIClient

from adforge import file_store
from adforge.retry import OutsideServiceDown
from agents import fixing_agent, pause, tasks
from agents.models import SystemNote, ToolCall
from chat.models import Message, Session
from gateway.fake import FakeModel, meanwhile, turn
from gateway.models import ModelCall
from gateway.types import Said, StepFinished
from jobs import owner_answers, stale, work
from jobs.library import LookFrame, OwnerPhoto, library_of, sent_list_of
from jobs.models import AgentFix, Job, OwnerQuestion, ProducedItem, ProductVideo
from jobs.reference_photos import clean_photo_of

from .conftest import (
    LABELLED,
    NOTHING_WATCHED,
    HeldSteps,
    WorkerStopped,
    picture,
    recorded,
)
from .sent_lists import piece
from .test_fixing_agent import POURS, film_again
from .test_fixing_agent_ask_owner import QUESTION, ask
from .test_producer_auto_fix import FLOATS, clip_of_scene_2, failing
from .test_producer_broll import (  # noqa: F401 (a fixture)
    assemble_through_the_chat,
    calling,
    checked,
    run,
)
from .test_product_only import failing as cut_out_failing
from .test_recolour import RECOLOUR_OK

pytestmark = pytest.mark.django_db(transaction=True)

WORDS = "No video, sorry: here is a photo of the lid screwed on."
PROMPT = "A hand screws the lid onto the mug and lets go."


GAVE_UP = ("give_up", {"reason": "The lid can't be filmed going on.", "best_try": 1})


def gave_up(fake_model: FakeModel, steps: HeldSteps, say: Callable[..., None]) -> ProducedItem:
    """Scene 2's clip and its first try fail the judge, the agent asks the shop owner for a
    video, keeps trying and gives up, and the ad is assembled with the last try. Gives the
    ad."""
    fake_model.respond("check_clip_motion", *[failing(FLOATS)] * 2)
    fake_model.respond(
        "fix_scene",
        turn(calls=[film_again(video_prompt=POURS)]),
        turn(calls=[ask()]),
        turn(calls=[GAVE_UP]),
    )
    return assemble_through_the_chat(fake_model, steps, say)


def labelling_calls() -> int:
    return ModelCall.objects.filter(purpose="label_photo").count()


def fix_turns(fake_model: FakeModel) -> list[Any]:
    return [each for each in fake_model.turns_taken if each.purpose == "fix_scene"]


def answer_read(fake_model: FakeModel) -> list[str]:
    """The shop owner's answer as the agent read it on its newest turn, line by line."""
    [read] = [
        each
        for each in fix_turns(fake_model)[-1].handoff.conversation
        if isinstance(each, Said) and each.text.startswith("The shop owner answered")
    ]
    return read.text.splitlines()


def the_question() -> OwnerQuestion:
    question = work.open_question(Job.objects.get().scenes.get(number=2))
    assert question is not None and question.question == QUESTION
    return question


def a_photo() -> str:
    return file_store.save("chat/lid-on.png", picture(40, 30, (20, 120, 40)))


def test_a_photo_after_a_give_up_starts_a_new_fix_that_films_with_it_into_the_ad(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    api: APIClient,
    session_id: str,
) -> None:
    first_ad = gave_up(fake_model, steps, say)
    job = Job.objects.get()
    question = the_question()
    photo = a_photo()
    number = len(library_of(job)) + 1
    references = {"pictures": [{"number": number, "used_for": "state"}], "piece": None}
    fake_model.respond(
        "fix_scene", turn(calls=[film_again(references=references, video_prompt=PROMPT)])
    )
    # The photo's colour isn't the clip's, so it is recoloured to it before it is sent.
    fake_model.respond("check_recolour", RECOLOUR_OK)
    turns_before = len([each for each in fake_model.turns_taken if each.purpose == "produce"])

    handed = owner_answers.answer(question, words=WORDS, photos=[photo])
    # Told the scene's clip changed in the ad shown, the producer assembles it again: code
    # doesn't (one new ad, not two).
    fake_model.respond("produce", turn(calls=[("assemble_ad", {})]), turn(says="Done."))
    steps.run_held()

    assert handed.kind == "new_fix"
    # The photo is the library's next item, as the shop owner's photo: the clean picture
    # drawn from it once, never the photo as sent (CONTEXT "Reference photo").
    [item] = [each for each in library_of(job) if each.number == number]
    assert item.source == OwnerPhoto(original=photo)
    [drawn] = ModelCall.objects.filter(purpose="make_reference_photo_picture")
    assert drawn.handoff["pictures"] == [photo]
    # Of only the thing that answers the question asked, not of everything the photo shows.
    assert drawn.handoff["prompt"] == clean_photo_of(
        f"the thing the photo shows that answers this question: {QUESTION}"
    )
    assert drawn.output == {"file": item.file}
    assert item.file != photo
    # The agent read that its fix starts again, the words and the new item.
    read = answer_read(fake_model)
    assert "it starts again now, from what they sent, with new tries." in read[0]
    assert f'They wrote: "{WORDS}"' in read
    assert any(f"library item {number}" in line for line in read)
    # It filmed again naming the item, with tries of its own; that try passed, and is scene
    # 2's clip in a new ad.
    fixing = AgentFix.objects.get()
    assert fixing.cap == 1 + fixing_agent.MOST_TRIES
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
    assert len(produced) == turns_before + 2
    told = [
        each.text for each in produced[-2].handoff.conversation if isinstance(each, StepFinished)
    ]
    assert "once the shop owner answered its question, the judge passed try 2." in told[-1]
    assert "assemble the ad again to show it." in told[-1]
    # The scene's card no longer shows the question; the chat thanked the shop owner once.
    shown = api.get(f"/api/sessions/{session_id}/messages/").json()
    assert all(not each["question"]["open"] for each in shown if each["question"])
    thanks = Message.objects.filter(text__startswith="Thanks, scene 2's clip is made again")
    assert thanks.filter(owner_question=question, step_state="started").count() == 1


def test_a_photo_while_the_fix_still_runs_reaches_its_next_try(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    fake_model.respond("check_clip_motion", *[failing(FLOATS)] * 3)
    photo = a_photo()
    handed: list[owner_answers.Handed] = []
    number: list[int] = []

    def answers() -> None:
        number.append(len(library_of(Job.objects.get())) + 1)
        handed.append(owner_answers.answer(the_question(), words=WORDS, photos=[photo]))

    def films_with_it() -> Any:
        references = {"pictures": [{"number": number[0], "used_for": "state"}], "piece": None}
        return turn(calls=[film_again(references=references, video_prompt=PROMPT)])

    fake_model.respond(
        "fix_scene",
        turn(calls=[film_again(video_prompt=POURS)]),
        turn(calls=[ask()]),
        # The shop owner answers while the agent films try 2.
        meanwhile(answers, turn(calls=[film_again()])),
        lambda: films_with_it(),
    )
    fake_model.answer_unscripted("check_recolour", lambda _: RECOLOUR_OK)

    assemble_through_the_chat(fake_model, steps, say)

    assert [each.kind for each in handed] == ["running"]
    # The agent read the answer before its next turn, the fix going on as it was.
    read = answer_read(fake_model)
    assert read[0] == "The shop owner answered your question, while you were working."
    assert any(f"library item {number[0]}" in line for line in read)
    fixing = AgentFix.objects.get()
    passed = fixing.tries.get(number=3).clip
    assert fixing.passed == passed and passed is not None
    assert fixing.cap == fixing_agent.MOST_TRIES
    sent = sent_list_of(passed)
    assert sent is not None
    assert number[0] in [each.library_number for each in sent.pictures] + [
        each.of for each in sent.recolours
    ]
    question = OwnerQuestion.objects.get()
    assert question.answered_at is not None and owner_answers.answered(fixing.step)


def test_a_photo_that_comes_as_the_agent_gives_up_opens_the_fix_again_in_the_same_step(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    fake_model.respond("check_clip_motion", *[failing(FLOATS)] * 2)
    photo = a_photo()
    fake_model.respond(
        "fix_scene",
        turn(calls=[film_again(video_prompt=POURS)]),
        turn(calls=[ask()]),
        meanwhile(
            lambda: owner_answers.answer(the_question(), words=WORDS, photos=[photo]),
            turn(calls=[GAVE_UP]),
        ),
        turn(calls=[film_again()]),
    )

    assemble_through_the_chat(fake_model, steps, say)

    fixing = AgentFix.objects.get()
    assert fixing.passed == fixing.tries.get(number=2).clip
    assert "it starts again now" in answer_read(fake_model)[0]


def test_a_photo_after_the_scenes_clip_passed_only_enters_the_library(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    fake_model.respond("check_clip_motion", failing(FLOATS))
    fake_model.respond("fix_scene", turn(calls=[ask()]), turn(calls=[film_again()]))
    assemble_through_the_chat(fake_model, steps, say)
    job = Job.objects.get()
    in_the_library = len(library_of(job))
    turns = len(fix_turns(fake_model))

    handed = owner_answers.answer(the_question(), words=WORDS, photos=[a_photo()])

    assert handed.kind == "library"
    assert any(f"library item {in_the_library + 1}" in line for line in handed.said)
    assert len(library_of(job)) == in_the_library + 1
    assert steps.held == [] and len(fix_turns(fake_model)) == turns
    assert OwnerQuestion.objects.get().answered_at is not None


def test_a_no_changes_nothing_and_closes_the_question(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    gave_up(fake_model, steps, say)
    job = Job.objects.get()
    in_the_library = len(library_of(job))
    in_the_ad = job.scenes.get(number=2).produced.filter(kind="clip").order_by("id").last()
    turns = len(fix_turns(fake_model))
    question = the_question()

    handed = owner_answers.answer(question, words="I don't have a video like that.")

    assert handed.kind == "nothing"
    # The question takes one answer.
    with pytest.raises(owner_answers.NotWaiting):
        owner_answers.answer(question, words="Half a turn.", photos=[a_photo()])
    assert steps.held == [] and len(fix_turns(fake_model)) == turns
    assert len(library_of(job)) == in_the_library
    assert job.scenes.get(number=2).produced.filter(kind="clip").order_by("id").last() == in_the_ad
    assert work.open_question(job.scenes.get(number=2)) is None
    assert not owner_answers.answered(AgentFix.objects.get().step)


def test_a_photo_no_clean_picture_passes_for_is_said_and_the_new_fix_goes_on_without_it(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    gave_up(fake_model, steps, say)
    job = Job.objects.get()
    in_the_library = len(library_of(job))
    fake_model.respond(
        "check_cut_out",
        cut_out_failing("A hand is still in it."),
        cut_out_failing("The lid is changed."),
        cut_out_failing("The handle is missing."),
    )
    fake_model.respond("fix_scene", turn(calls=[film_again()]))

    owner_answers.answer(the_question(), words=WORDS, photos=[a_photo()])
    run(fake_model, steps)

    # Nothing entered the library, and the shop owner was told, in plain words, that their
    # photo couldn't be used.
    assert len(library_of(job)) == in_the_library
    told = (
        "I couldn't use a photo you sent, so I'm going on without it. Send another photo if "
        "you'd like it used."
    )
    job.refresh_from_db()
    assert [each["level"] for each in job.warnings if each["text"] == told] == ["info"]
    assert Message.objects.filter(role="notice", text=told).count() == 1
    # The agent read why, and went on: its try passed.
    assert (
        "Their photo isn't in the library: all 3 tries failed (A hand is still in it. The lid "
        "is changed. The handle is missing.)."
    ) in answer_read(fake_model)
    fixing = AgentFix.objects.get()
    assert fixing.passed == fixing.tries.get(number=2).clip


def test_a_photo_whose_clean_picture_check_gives_no_answer_is_kept_unchecked_and_recorded(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    api: APIClient,
    session_id: str,
) -> None:
    gave_up(fake_model, steps, say)
    job = Job.objects.get()
    number = len(library_of(job)) + 1
    photo = a_photo()
    # Down through every retry of the one ask.
    fake_model.respond(
        "check_cut_out", *[OutsideServiceDown("the vision service answered 503")] * 3
    )
    fake_model.respond("fix_scene", turn(calls=[film_again()]))

    owner_answers.answer(the_question(), words=WORDS, photos=[photo])
    run(fake_model, steps)

    # The clean picture is kept, unchecked, and that is recorded in red, never silent.
    [item] = [each for each in library_of(job) if each.number == number]
    assert item.source == OwnerPhoto(original=photo)
    said = (
        "Checking the clean picture of the thing the photo shows that answers this question: "
        f"{QUESTION} gave no answer (the AI service checking it didn't answer), so it is kept "
        "unchecked."
    )
    assert ("problem", said) in recorded(api, session_id)
    assert not Message.objects.filter(text=said).exists()


def test_a_video_is_watched_and_its_moments_and_look_frames_reach_the_agent(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    gave_up(fake_model, steps, say)
    job = Job.objects.get()
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

    owner_answers.answer(the_question(), words="", videos=[video])
    run(fake_model, steps)

    [kept] = ProductVideo.objects.filter(file=video)
    assert len(fake_model.videos_watched) == watched_before + 1
    [frame] = [each for each in library_of(job) if each.number == number]
    assert frame.source == LookFrame(video=kept.position, second=1.0)
    assert answer_read(fake_model)[1:3] == [
        "They wrote no words.",
        f"Their video is page video {kept.position}, watched: moments 1 to 1. Its look frames "
        f"are library items {number}.",
    ]


def test_a_worker_restart_while_taking_the_answer_runs_the_step_again_adding_the_photo_once(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gave_up(fake_model, steps, say)
    job = Job.objects.get()
    in_the_library = len(library_of(job))
    labelled_before = labelling_calls()
    resume = fixing_agent.answer_question

    def stops_once(*args: Any) -> Any:
        monkeypatch.setattr(fixing_agent, "answer_question", resume)
        raise WorkerStopped()

    monkeypatch.setattr(fixing_agent, "answer_question", stops_once)
    question = the_question()
    owner_answers.answer(question, words=WORDS, photos=[a_photo()])
    step = AgentFix.objects.get().step
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
    assert fixing.passed == fixing.tries.get(number=2).clip
    assert len(library_of(job)) == in_the_library + 1
    assert labelling_calls() == labelled_before + 1
    assert Message.objects.filter(text__startswith="Thanks, scene 2's clip").count() == 1


def test_an_error_after_the_answer_keeps_the_last_try_in_the_ad(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    ad = gave_up(fake_model, steps, say)
    job = Job.objects.get()
    last_try = AgentFix.objects.get().tries.get(number=1).clip
    assert last_try is not None
    fake_model.respond("fix_scene", RuntimeError("The agent's model broke."))

    owner_answers.answer(the_question(), words=WORDS, photos=[a_photo()])
    run(fake_model, steps)

    # The ad keeps the last try, never the first clip the judge failed.
    ads = job.produced.filter(kind=ProducedItem.Kind.FINISHED_AD).order_by("version")
    shown = ads.last()
    assert shown is not None and shown.pk == ad.pk
    newest = job.scenes.get(number=2).produced.filter(kind="clip").order_by("id").last()
    assert newest is not None and newest.file == last_try.file
    # The shop owner is shown every try, as for a give-up.
    assert Message.objects.filter(tries_of=AgentFix.objects.get().step).exists()


def test_a_resumed_step_the_worker_stops_in_twice_ends_keeping_its_clip_in_the_ad(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ad = gave_up(fake_model, steps, say)
    job = Job.objects.get()
    first = ProducedItem.objects.get(pk={cut["scene"]: cut["clip"] for cut in ad.cuts}[2])

    def stops(*args: Any) -> Any:
        raise WorkerStopped()

    # The resume stops the worker every time.
    monkeypatch.setattr(fixing_agent, "answer_question", stops)
    owner_answers.answer(the_question(), words=WORDS, photos=[a_photo()])
    step = AgentFix.objects.get().step
    with pytest.raises(WorkerStopped):
        steps.run_held()
    tasks.carry_on_after_the_worker_starts()
    with pytest.raises(WorkerStopped):
        steps.run_held()
    fake_model.respond("produce", turn(says="Done."))

    tasks.carry_on_after_the_worker_starts()

    # Queued again once only: the second time it ends, never failed, recorded in red in the
    # warnings for the developer, and not said in the shop owner's chat (spec #272).
    step.refresh_from_db()
    assert (step.status, steps.held) == ("finished", [])
    said = (
        "Scene 2's fix couldn't carry on with your answer: its work stopped partway again "
        "after it was started again, so the clip that was in the ad stays there."
    )
    assert not Message.objects.filter(text=said).exists()
    assert [each["level"] for each in Job.objects.get().warnings if each["text"] == said] == [
        "problem"
    ]
    # The scene keeps its clip in the ad, and isn't planned again: make_clip makes nothing.
    scene = job.scenes.get(number=2)
    assert scene.status == "finished"
    current = stale.current_clip(scene)
    assert current is not None and current.file == first.file
    # The scene's card shows its clip made, not still being made.
    card = Message.objects.filter(step_scene=2, step_kind="clip").latest("id")
    assert card.step_state == "made"
    # The producer is told so, never to make the clip again.
    assert f"(version {first.version}) stays in the ad" in step.result
    assert "make_clip" not in step.result
    assert "nothing was made or paid for again" in already_made(fake_model, say)


def already_made(fake_model: FakeModel, say: Callable[..., None]) -> str:
    """What make_clip answers for scene 2, called through the chat."""
    calling(fake_model, say, ("make_clip", {"scene": 2}))
    return ToolCall.objects.filter(tool="make_clip").latest("id").result


def test_a_pause_before_the_worker_takes_an_answers_new_fix_keeps_it_and_its_clip(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    session_id: str,
) -> None:
    ad = gave_up(fake_model, steps, say)
    job = Job.objects.get()
    in_the_ad = ProducedItem.objects.get(pk={cut["scene"]: cut["clip"] for cut in ad.cuts}[2])
    question = the_question()
    assert owner_answers.answer(question, words=WORDS, photos=[a_photo()]).kind == "new_fix"
    step = AgentFix.objects.get().step

    pause.pause(Session.objects.get(pk=session_id), wake=False)

    # The step carrying the answer isn't cancelled away with it.
    step.refresh_from_db()
    assert step.status == "running"
    [note] = SystemNote.objects.filter(kind=SystemNote.Kind.PAUSED)
    cancelled, running = note.text.split("Still running")
    assert "scene 2" not in cancelled
    assert "carry it on with fix_scene for scene 2." in running

    # The worker takes it: the answer is taken, and the fix parks for the pause before the
    # agent takes a turn.
    turns_before = len(fix_turns(fake_model))
    fake_model.respond("produce", turn(says="Paused."))
    steps.run_held()

    step.refresh_from_db()
    assert step.status == "finished"
    question.refresh_from_db()
    assert question.answered_at is not None
    assert AgentFix.objects.get(step=step).paused_at is not None
    assert len(fix_turns(fake_model)) == turns_before
    current = stale.current_clip(job.scenes.get(number=2))
    assert current is not None and current.file == in_the_ad.file
    assert "nothing was made or paid for again" in already_made(fake_model, say)


def test_a_late_answer_to_a_fix_that_passed_only_enters_the_library(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    fake_model.respond("check_clip_motion", failing(FLOATS))
    fake_model.respond("fix_scene", turn(calls=[ask()]), turn(calls=[film_again()]))
    assemble_through_the_chat(fake_model, steps, say)
    job = Job.objects.get()
    in_the_library = len(library_of(job))
    step = AgentFix.objects.get().step
    # Handed in as the passing try was judged, too late for the fix to take it.
    OwnerQuestion.objects.update(answer={"words": WORDS, "photos": [a_photo()], "videos": []})

    with transaction.atomic():
        owner_answers.run_with_a_late_answer(step)

    assert len(library_of(job)) == in_the_library + 1
    step.refresh_from_db()
    assert (step.status, steps.held) == ("finished", [])
    assert Message.objects.filter(text__contains="put what you sent in the ad's library").exists()


def test_a_late_answer_to_a_fix_that_gave_up_runs_its_step_again(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    gave_up(fake_model, steps, say)
    step = AgentFix.objects.get().step
    OwnerQuestion.objects.update(answer={"words": WORDS, "photos": [a_photo()], "videos": []})

    with transaction.atomic():
        owner_answers.run_with_a_late_answer(step)

    step.refresh_from_db()
    assert (step.status, steps.held) == ("running", [step.pk])
    assert owner_answers.resuming(step)


def test_a_photo_for_a_fix_the_pause_parked_waits_until_it_carries_on(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    session_id: str,
) -> None:
    session = Session.objects.get(pk=session_id)
    fake_model.respond("check_clip_motion", *[failing(FLOATS)] * 2)
    fake_model.respond(
        "fix_scene",
        turn(calls=[ask()]),
        # The shop owner pauses while try 1 is filmed: the fix parks after it.
        meanwhile(lambda: pause.pause(session, wake=False), turn(calls=[film_again()])),
    )
    clip_of_scene_2(fake_model, steps, say)
    fixing = AgentFix.objects.get()
    assert fixing.paused_at is not None

    handed = owner_answers.answer(the_question(), words=WORDS, photos=[a_photo()])

    assert handed.kind == "running" and steps.held == []
    # Nothing is taken while the pause holds.
    assert owner_answers.take_the_answer(fixing.step) is None
    assert OwnerQuestion.objects.get().answered_at is None

    # Carried on, the fix takes it before its next turn.
    fake_model.respond("fix_scene", turn(calls=[film_again()]))
    calling(fake_model, say, ("fix_scene", {"scene": 2, "instruction": "Carry on."}))
    run(fake_model, steps)

    assert answer_read(fake_model)[0].startswith("The shop owner answered your question")
    assert OwnerQuestion.objects.get().answered_at is not None
