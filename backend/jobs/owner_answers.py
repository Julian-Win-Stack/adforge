"""The shop owner's answer to the fixing agent's question (spec #210 point 23, ticket #245,
as the round 7 spec's section 4 changed them, Julian 2026-10-30: "Keep trying while it
waits").

The fixing agent's last resort at a judge-failed scene is one question for the shop owner
(ask_owner): it goes to them in the chat, and the fix keeps trying; the ad never waits for
the answer. The answer comes in through `answer`, called with what the owner said and sent in
the chat (the producer calls it, spec #247; the scene's card shows the question as text
only). Only a photo or a video changes anything: words alone, as a "no", close the question
and change nothing, so they never become the agent's reason to stop. What they sent is put
into the ad's library by `take_the_answer`: each photo as the shop owner's photo, labelled,
with its product-only version when it needs one; each video watched by the watcher, its
moments kept and its look frames in the library. Then: a fix still running is told of it
before its next turn; a fix that ended with no try passing is opened again from it, in its
clip step run again in a worker, with new tries (`fixing_agent.answer_question`); a scene
whose clip passed only gets it in the library. Whatever came is the answer, even when it
isn't what was asked for. Once the step finishes, its clip replaces the scene's and the ad
is assembled again, as a swapped-in try's is (agents.tasks)."""

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

from django.db import transaction
from django.utils import timezone

from chat import messages
from chat.models import Message

from . import cut_out, library_items, notices, product_videos, reference_photos, watcher, work
from .job_locks import one_job_at_a_time
from .library import LibraryEntry, LookFrame, library_of, watch_of
from .models import AgentFix, Job, OwnerQuestion, ProductVideo, Scene, SceneStep
from .notices import post_notice

logger = logging.getLogger(__name__)


class NotWaiting(Exception):
    """The question takes no answer now, saying why."""


@dataclass(frozen=True)
class Handed:
    """What became of the shop owner's answer: it goes to the fix still running (`running`),
    a new fix of the scene starts from it (`new_fix`), it only went into the library as the
    scene's clip passed (`library`, with a line on each file), or, with no photo or video,
    nothing changes (`nothing`)."""

    kind: Literal["running", "new_fix", "library", "nothing"]
    said: Sequence[str] = ()


def answer(
    question: OwnerQuestion,
    *,
    words: str,
    photos: Sequence[str] = (),
    videos: Sequence[str] = (),
    instruction: str = "",
) -> Handed:
    """Hand the shop owner's answer to `question` on: their `words`, and the keys in the
    file store of the `photos` and `videos` they sent with them, and the producer's
    `instruction` on what to do with it, handed on after them. With no photo or video, the
    question is closed and nothing changes. Else, for a fix still running (or waiting on the
    shop owner's pause) the answer waits for its next turn; for a fix that ended with no try
    passing, its clip step runs again in a worker, which puts the files in the library and
    opens the fix again from them, and the chat says the scene's clip is made again; for a
    scene whose clip passed, the files only go into the library, here. Gives what became of
    it. Raises NotWaiting when the question was answered already or withdrawn, or the scene
    has a newer version than the clip the ended fix left in the ad; ValueError for an answer
    with nothing in it."""
    words = words.strip()
    if not (words or photos or videos):
        raise ValueError("an answer has words, a photo or a video")
    given = {
        "words": words,
        "photos": list(photos),
        "videos": list(videos),
        "instruction": instruction.strip(),
    }
    with transaction.atomic():
        # The job, then the chat, as every writer of a step's end locks them.
        job = Job.objects.select_for_update().get(pk=question.scene.job_id)
        found = OwnerQuestion.objects.select_for_update().filter(pk=question.pk).first()
        if found is None or found.answered_at is not None or found.answer is not None:
            raise NotWaiting("the question was answered already, or withdrawn.")
        number = found.scene.number
        if not (photos or videos):
            # A "no", or words alone: nothing changes (round 7 spec section 4).
            found.answer = given
            found.answered_at = timezone.now()
            found.save(update_fields=["answer", "answered_at"])
            return Handed("nothing")
        fixing = AgentFix.objects.get(pk=found.agent_fix_id)
        step = SceneStep.objects.select_for_update().get(agent_fix=fixing)
        if step.status == SceneStep.Status.RUNNING or fixing.paused_at is not None:
            found.answer = given
            found.save(update_fields=["answer"])
            post_notice(
                job,
                notices.REPLY,
                f"Thanks, scene {number}'s fix gets what you sent.",
                Message.Level.INFO,
            )
            return Handed("running")
        if fixing.passed_id is None:
            # A new fix starts from the clip the ended one left in the ad: a scene changed
            # since, as by a plan or a swap, has moved past the question.
            shown = work.versions(found.scene)
            if not shown or shown[-1].clip is None or shown[-1].clip.pk != fixing.kept_id:
                raise NotWaiting("the scene has a newer version since the question was asked.")
            found.answer = {**given, "resumes": True}
            found.save(update_fields=["answer"])
            _run_again(step)
            post_notice(
                job,
                notices.REPLY,
                f"Thanks, scene {number}'s clip is made again from what you sent.",
                Message.Level.INFO,
                step=messages.StepSignal(number, Message.StepKind.CLIP, Message.StepState.STARTED),
                owner_question=found.pk,
            )
            return Handed("new_fix")
        found.answer = given
        found.save(update_fields=["answer"])
    # The scene's clip passed: what came only goes into the library, as add_to_library puts
    # it, here.
    taken = _only_into_the_library(step, found)
    return Handed("library", taken.said if taken is not None else ())


def _only_into_the_library(step: SceneStep, question: OwnerQuestion) -> Taken | None:
    """Put the answer to `question`, whose fix passed, into the library only, and tell the
    shop owner so."""
    taken = take_the_answer(step)
    post_notice(
        question.scene.job,
        notices.REPLY,
        f"Thanks, I put what you sent in the ad's library. Scene {question.scene.number}'s "
        "clip passed its check already, so it stays as it is.",
        Message.Level.INFO,
    )
    return taken


def _run_again(step: SceneStep) -> None:
    """Run `step` again in a worker, its fix carrying on from its checkpoints, as in any
    step's work."""
    SceneStep.objects.filter(pk=step.pk).update(
        status=SceneStep.Status.RUNNING,
        reason="",
        result="",
        finished_at=None,
        producer_read_at=None,
        taken_at=None,
        seen_at=timezone.now(),
    )
    # The worker's tasks run the job's steps, so they are imported where they are used.
    from agents import tasks

    step_id = step.pk
    transaction.on_commit(lambda: tasks.run_scene_step.delay(step_id))


def run_with_a_late_answer(step: SceneStep) -> None:
    """For a clip step just finished: when an answer handed in while its fix ran wasn't taken
    before the fix ended, run the step again for it, as for an answer to an ended fix; for a
    fix that passed, what came only goes into the library, once the step's end is committed.
    A fix the shop owner's pause parked takes it when it carries on."""
    late = (
        OwnerQuestion.objects.filter(
            agent_fix__step=step,
            agent_fix__paused_at__isnull=True,
            answered_at__isnull=True,
            answer__isnull=False,
        )
        .select_related("agent_fix", "scene__job")
        .first()
    )
    if late is None:
        return
    if late.agent_fix.passed_id is not None:
        transaction.on_commit(lambda: _only_into_the_library(step, late))
        return
    late.answer = {**(late.answer or {}), "resumes": True}
    late.save(update_fields=["answer"])
    _run_again(step)


def resuming(step: SceneStep) -> bool:
    """Whether `step` is a clip step run again with the shop owner's answer to its fix's
    question (`answer`): a worker that stops while it runs leaves it to be run again."""
    return OwnerQuestion.objects.filter(agent_fix__step=step, answer__resumes=True).exists()


# How many times a step run again with the shop owner's answer is queued again after the
# worker stopped partway through it; past that it fails, so a resume that stops the worker
# every time doesn't run forever.
MOST_RUNS_AGAIN = 1


def run_again(step: SceneStep) -> bool:
    """For `step`, run again with the shop owner's answer and stopped partway, as when the
    worker restarted: queue it to run again, at most MOST_RUNS_AGAIN times, the fix and the
    answer carrying on from their checkpoints, paying for nothing twice, its clip from before
    the answer staying in the ad meanwhile. Past that, nothing is queued: the chat is told the
    fix couldn't carry on, and False is given, for the step to fail as any stopped step does.
    Gives whether it was queued."""
    question = (
        OwnerQuestion.objects.select_for_update()
        .filter(agent_fix__step=step, answer__resumes=True)
        .select_related("scene__job")
        .get()
    )
    given: dict[str, Any] = question.answer or {}
    runs = int(given.get("runs_again", 0))
    if runs >= MOST_RUNS_AGAIN:
        logger.warning(
            "Scene step %s, resumed with the shop owner's answer, stopped again: failed", step.pk
        )
        post_notice(
            question.scene.job,
            notices.FIX_NOT_CARRIED_ON,
            f"Scene {question.scene.number}'s fix couldn't carry on with your answer: its work "
            "stopped partway again after it was started again, so the clip that was in the ad "
            "stays there.",
            Message.Level.PROBLEM,
            scene=question.scene.number,
        )
        return False
    question.answer = {**given, "runs_again": runs + 1}
    question.save(update_fields=["answer"])
    SceneStep.objects.filter(pk=step.pk).update(taken_at=None, seen_at=timezone.now())
    from agents import tasks

    step_id = step.pk
    transaction.on_commit(lambda: tasks.run_scene_step.delay(step_id))
    return True


def withdraw(scene: Scene) -> None:
    """Withdraw the scene's open question, not answered yet, as a change to the scene starts:
    a newer version moves past it. The chat says so, once."""
    for question in OwnerQuestion.objects.filter(
        scene=scene, answered_at__isnull=True, answer__isnull=True
    ):
        question.delete()
        post_notice(
            scene.job,
            notices.REPLY,
            f"I no longer need an answer to my question about scene {scene.number}'s clip: "
            "the scene is being changed.",
            Message.Level.INFO,
        )


def answered(step: SceneStep) -> bool:
    """Whether `step`'s fix was handed the shop owner's answer to its question."""
    return OwnerQuestion.objects.filter(agent_fix__step=step, answer__taken=True).exists()


def take_the_answer(step: SceneStep) -> Taken | None:
    """For a clip step whose fix's question has an answer waiting: put what the shop owner
    sent into the library, then hand it to the fix (`fixing_agent.answer_question`), which,
    if it ended with no try passing, is opened again from it; a fix that passed only gets it
    in the library. Nothing for a step with no answer waiting, or whose fix waits on the
    shop owner's pause: it takes it once carried on. Run again, as after a worker stopped, it
    adds nothing twice and pays for nothing twice. Gives what the files added, or None."""
    question = (
        OwnerQuestion.objects.filter(
            agent_fix__step=step, answered_at__isnull=True, answer__isnull=False
        )
        .select_related("agent_fix")
        .first()
    )
    if question is None:
        return None
    fixing = question.agent_fix
    if fixing.paused_at is not None and fixing.finished_at is not None:
        return None
    job = step.scene.job
    given: dict[str, Any] = question.answer or {}
    taken = into_the_library(
        job,
        photos=given["photos"],
        videos=given["videos"],
        shows=f"what the shop owner sent in answer to this question: {question.question}",
        cleaned_of=f"the thing the photo shows that answers this question: {question.question}",
        without_it="the fixing agent goes on without it",
    )
    # The fixing agent's module runs the job's work, so it is imported where it is used.
    from agents import fixing_agent

    with transaction.atomic():
        say_unreadable(job, taken.unreadable)
        if fixing.passed_id is not None:
            OwnerQuestion.objects.filter(pk=question.pk).update(answered_at=timezone.now())
            return taken
        again = fixing.finished_at is not None
        text = "\n".join(
            [
                "The shop owner answered your question"
                + (
                    ". Your fix had ended with no try passing: it starts again now, from what "
                    "they sent, with new tries."
                    if again
                    else ", while you were working."
                ),
                f'They wrote: "{given["words"]}"' if given["words"] else "They wrote no words.",
                *taken.said,
                "This is their answer, whether or not it is what you asked for: open what "
                "they sent with look_at_library, and use it in your next try.",
                *(
                    [f"The producer, who talks with them, adds: {given['instruction']}"]
                    if given.get("instruction")
                    else []
                ),
            ]
        )
        fixing_agent.answer_question(question, text)
        OwnerQuestion.objects.filter(pk=question.pk).update(answer={**given, "taken": True})
    return taken


@dataclass(frozen=True)
class Taken:
    """What the shop owner's files added to the library: the items, the videos kept and
    watched (a piece can be cut from their moments, look frames or not), a line for the
    agent on each file, and how many videos couldn't be read as videos."""

    added: list[LibraryEntry]
    watched: list[ProductVideo]
    said: list[str]
    unreadable: int


def into_the_library(
    job: Job,
    *,
    photos: Sequence[str],
    videos: Sequence[str],
    shows: str,
    cleaned_of: str,
    without_it: str,
) -> Taken:
    """Put the photos and videos the shop owner sent in the chat (file-store keys) into
    `job`'s library: each photo as the clean picture of only `cleaned_of` drawn from it
    (never the raw photo: CONTEXT "Reference photo"), labelled as kept to show `shows` (a
    clean picture needs no product-only version); each video kept and watched, its look
    frames added. A photo no clean picture passes for is said to the shop owner, and
    `without_it`, what happens without it, is logged. Run again, it adds nothing twice, pays
    for nothing twice and says nothing twice."""
    said: list[str] = []
    added: list[LibraryEntry] = []
    for key in photos:
        drawn = _clean_picture(job, key, cleaned_of, without_it)
        if isinstance(drawn, Unusable):
            said.append(f"Their photo isn't in the library: {drawn.why}.")
            continue
        item = library_items.add_owner_photo(job, key, drawn, shows)
        if item is None:
            # The labelling call's red notice told the shop owner.
            said.append("Their photo couldn't be labelled, so it isn't in the library.")
            continue
        added.append(item)
        said.append(f"Their photo is library item {library_items.library_line(item)}")
    unreadable, watched, from_videos = _videos(job, videos, added)
    return Taken(added, watched, said + from_videos, unreadable)


@dataclass(frozen=True)
class Unusable:
    """Why no clean picture could be drawn from a photo, for the agent."""

    why: str


def _clean_picture(job: Job, photo: str, cleaned_of: str, without_it: str) -> str | Unusable:
    """The clean picture of only `cleaned_of` drawn from the shop owner's photo at `photo`,
    a cut-out checked by the cut-out check (asked once) and drawn again when it fails, as
    the clean picture of their thing on a fix step is (work.clean_picture). Gives its key, or
    why none passed: the shop owner is asked, in plain words, for another photo, and the
    server log says why and `without_it`, what happens without it. Run again, it
    pays for nothing already paid for and says nothing it said."""
    with one_job_at_a_time(work.CLEAN_PICTURE_LOCK, job):
        try:
            return cut_out.redraw_with_check(
                job,
                purpose="make_reference_photo_picture",
                prompt=reference_photos.clean_photo_of(cleaned_of),
                photo=photo,
                check=work.owner_photo_check(
                    job,
                    photo,
                    keep=cleaned_of,
                    named=f"the clean picture of {cleaned_of}",
                    photo_named="the shop owner's photo",
                ),
                unchecked_passes=True,
            )
        except cut_out.CutOutFailed as failed:
            why = failed.why()
    logger.warning(
        "No clean picture from the shop owner's photo %s: %s; %s", photo, why, without_it
    )
    text = (
        "I couldn't use a photo you sent, so I'm going on without it. Send another photo if "
        "you'd like it used."
    )
    if all(warning["text"] != text for warning in job.warnings):
        post_notice(job, notices.OWNER_FILE_UNUSABLE, text, Message.Level.INFO)
    return Unusable(why)


def say_unreadable(job: Job, unreadable: int) -> None:
    """Tell the shop owner in red of each video they sent that couldn't be read."""
    for _ in range(unreadable):
        post_notice(
            job,
            notices.OWNER_FILE_UNUSABLE,
            "I couldn't open the video you sent. Could you send it again, as an MP4 if you can?",
            Message.Level.INFO,
        )


def _videos(
    job: Job, keys: Sequence[str], added: list[LibraryEntry]
) -> tuple[int, list[ProductVideo], list[str]]:
    """Keep and watch the videos the shop owner sent, and only those, adding their look
    frames to `added`. Gives how many couldn't be read as videos, for the chat to be told
    with the answer, and what the agent is told of each."""
    kept: list[ProductVideo] = []
    watched: list[ProductVideo] = []
    said: list[str] = []
    unreadable = 0
    for key in keys:
        video = product_videos.keep_owner_video(job, key)
        if video is None:
            unreadable += 1
            said.append("Their video couldn't be read, so nothing from it is in the library.")
        else:
            kept.append(video)
    if kept:
        watcher.watch_videos(job, job.page_text, only=kept)
    for video in kept:
        video.refresh_from_db()
        watch = watch_of(video)
        if watch is None:
            # The watcher said why in the chat.
            why = video.thrown_out or "it couldn't be watched."
            said.append(f"Their video is left out of the library: {why}")
            continue
        frames = [
            item
            for item in library_of(job)
            if isinstance(item.source, LookFrame) and item.source.video == video.position
        ]
        added += frames
        moments = f"moments 1 to {len(watch.moments)}" if watch.moments else "no moment"
        looks = (
            f" Its look frames are library items {', '.join(str(each.number) for each in frames)}."
            if frames
            else ""
        )
        said.append(f"Their video is page video {video.position}, watched: {moments}.{looks}")
        watched.append(video)
    return unreadable, watched, said


def items_said(items: Sequence[LibraryEntry]) -> str:
    """Such as "item 9", or "items 9 and 10"."""
    numbers = [str(item.number) for item in items]
    if len(numbers) == 1:
        return f"item {numbers[0]}"
    return f"items {', '.join(numbers[:-1])} and {numbers[-1]}"
