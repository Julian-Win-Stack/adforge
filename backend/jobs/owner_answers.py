"""The shop owner's answer to the fixing agent's question (spec #210 point 23, ticket #245).

The fixing agent's last resort at a judge-failed scene is one question for the shop owner
(ask_owner): its fix is parked on it, the scene keeping its last try in the ad. The answer
comes in through `answer`, called with what the owner said and sent in the chat (the
producer calls it, spec #247; the scene's card shows the question as text only). It keeps
the answer on the question and runs the fix's clip step again in a worker. There
`take_the_answer` puts what came into the ad's library: each photo as the shop owner's
photo, labelled, with its product-only version when it needs one; each video watched by the
watcher, its moments kept and its look frames in the library. The words and the new item
numbers (or that no file came) become the parked ask_owner call's result, and the fix goes
on from its checkpoints (`fixing_agent.answer_question`). Whatever came is the answer, even
when it isn't what was asked for. Once the step finishes, its clip replaces the scene's and
the ad is assembled again, as a swapped-in try's is (agents.tasks)."""

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from django.db import transaction
from django.utils import timezone

from chat import messages
from chat.models import Message
from gateway.types import Image

from . import cut_out, library_items, product_videos, reference_photos, watcher, work
from .job_locks import one_job_at_a_time
from .library import LibraryEntry, LookFrame, ProductOnly, library_of, watch_of
from .models import Job, OwnerQuestion, ProductVideo, Scene, SceneStep
from .notices import post_notice

logger = logging.getLogger(__name__)


class NotWaiting(Exception):
    """The question takes no answer now, saying why."""


def answer(
    question: OwnerQuestion,
    *,
    words: str,
    photos: Sequence[str] = (),
    videos: Sequence[str] = (),
    instruction: str = "",
) -> SceneStep:
    """Hand the shop owner's answer to `question` to its parked fix: their `words`, and the
    keys in the file store of the `photos` and `videos` they sent with them, and the
    producer's `instruction` on what to do with it, handed on after them. The fix's clip
    step runs again in a worker, which puts the files in the library and resumes the fix with
    the answer; the chat says the scene's fix carries on, which clears the question from the
    scene's card. Gives the step. Raises NotWaiting when the question was answered already
    or withdrawn, its fix is carrying on with an answer now, or the scene has a newer version
    than the clip the fix left in the ad; ValueError for an answer with nothing in it."""
    words = words.strip()
    if not (words or photos or videos):
        raise ValueError("an answer has words, a photo or a video")
    with transaction.atomic():
        # The job, then the chat, as every writer of a step's end locks them.
        job = Job.objects.select_for_update().get(pk=question.scene.job_id)
        found = OwnerQuestion.objects.select_for_update().filter(pk=question.pk).first()
        if found is None or found.answered_at is not None:
            raise NotWaiting("the question was answered already, or withdrawn.")
        step = SceneStep.objects.select_for_update().get(agent_fix=found.agent_fix_id)
        if step.status == SceneStep.Status.RUNNING:
            raise NotWaiting("the scene's fix is carrying on with an answer already.")
        # The fix carries on from the clip it left in the ad: a scene changed since, as by a
        # plan or a swap, has moved past the question.
        shown = work.versions(found.scene)
        if not shown or shown[-1].clip is None or shown[-1].clip.pk != found.agent_fix.kept_id:
            raise NotWaiting("the scene has a newer version since the question was asked.")
        found.answer = {
            "words": words,
            "photos": list(photos),
            "videos": list(videos),
            "instruction": instruction.strip(),
        }
        found.save(update_fields=["answer"])
        # Its step runs again: the fix carries on in it, as in any step's work.
        SceneStep.objects.filter(pk=step.pk).update(
            status=SceneStep.Status.RUNNING,
            reason="",
            result="",
            finished_at=None,
            producer_read_at=None,
            taken_at=None,
            seen_at=timezone.now(),
        )
        number = found.scene.number
        post_notice(
            job,
            f"Scene {number}'s fix carries on with your answer.",
            Message.Level.INFO,
            step=messages.StepSignal(number, Message.StepKind.CLIP, Message.StepState.STARTED),
            owner_question=found.pk,
        )
        # The worker's tasks run the job's steps, so they are imported where they are used.
        from agents import tasks

        step_id = step.pk
        transaction.on_commit(lambda: tasks.run_scene_step.delay(step_id))
    step.refresh_from_db()
    return step


def resuming(step: SceneStep) -> bool:
    """Whether `step` is a clip step run again with the shop owner's answer to its fix's
    question (`answer`): a worker that stops while it runs leaves it to be run again."""
    return OwnerQuestion.objects.filter(agent_fix__step=step, answer__isnull=False).exists()


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
        .filter(agent_fix__step=step, answer__isnull=False)
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
            f"Scene {question.scene.number}'s fix couldn't carry on with your answer: its work "
            "stopped partway again after it was started again, so the clip that was in the ad "
            "stays there.",
            Message.Level.PROBLEM,
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
            f"The fixing agent's question about scene {scene.number}'s clip is withdrawn: the "
            "scene is being changed, so it no longer needs your answer.",
            Message.Level.INFO,
        )


def answered(step: SceneStep) -> bool:
    """Whether `step`'s fix was parked on a question the shop owner has answered since."""
    return OwnerQuestion.objects.filter(agent_fix__step=step, answered_at__isnull=False).exists()


def take_the_answer(step: SceneStep) -> None:
    """For a clip step run again with the shop owner's answer: put what they sent into the
    library, and give the answer to the parked fix as its ask_owner call's result, so the
    fix goes on. Nothing for a step with no answer waiting. Run again, as after a worker
    stopped, it adds nothing twice and pays for nothing twice."""
    question = OwnerQuestion.objects.filter(
        agent_fix__step=step, answered_at__isnull=True, answer__isnull=False
    ).first()
    if question is None:
        return
    job = step.scene.job
    given: dict[str, Any] = question.answer or {}
    taken = into_the_library(
        job,
        photos=given["photos"],
        videos=given["videos"],
        shows=f"what the shop owner sent in answer to this question: {question.question}",
        cleaned_of=f"the thing the photo shows that answers this question: {question.question}",
    )
    added, said = taken.added, taken.said
    text = "\n".join(
        [
            f'The shop owner answered: "{given["words"]}"'
            if given["words"]
            else "The shop owner wrote no words.",
            *(said or ["The shop owner sent no file."]),
            "This is their answer, whether or not it is what you asked for: go on with it.",
            *(
                [f"The producer, who asked them for you, adds: {given['instruction']}"]
                if given.get("instruction")
                else []
            ),
        ]
    )
    pictures = [Image(f"Library item {item.number}: {item.label}", item.file) for item in added]
    # The fixing agent's module runs the job's work, so it is imported where it is used.
    from agents import fixing_agent
    from agents.loop import ToolResult

    with transaction.atomic():
        say_unreadable(job, taken.unreadable)
        if added:
            post_notice(
                job,
                f"What you sent is in the ad's picture library as {items_said(added)}, and the "
                f"fixing agent carries on with scene {step.scene.number}'s clip.",
                Message.Level.INFO,
            )
        fixing_agent.answer_question(question, ToolResult(text, pictures))


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
    job: Job, *, photos: Sequence[str], videos: Sequence[str], shows: str, cleaned_of: str
) -> Taken:
    """Put the photos and videos the shop owner sent in the chat (file-store keys) into
    `job`'s library: each photo as the clean picture of only `cleaned_of` drawn from it
    (never the raw photo: CONTEXT "Reference photo"), labelled as kept to show `shows`, with
    its product-only version when it needs one; each video kept and watched, its look frames
    added. Run again, it adds nothing twice and pays for nothing twice."""
    said: list[str] = []
    added: list[LibraryEntry] = []
    for key in photos:
        drawn = _clean_picture(job, key, cleaned_of)
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
    if added:
        library_items.add_product_only_versions(job)
        numbers = {item.number for item in added}
        for version in library_of(job):
            if isinstance(version.source, ProductOnly) and version.source.of in numbers:
                added.append(version)
                said.append(
                    f"Its product-only version is library item "
                    f"{library_items.library_line(version)}"
                )
    unreadable, watched, from_videos = _videos(job, videos, added)
    return Taken(added, watched, said + from_videos, unreadable)


@dataclass(frozen=True)
class Unusable:
    """Why no clean picture could be drawn from a photo, for the agent."""

    why: str


def _clean_picture(job: Job, photo: str, cleaned_of: str) -> str | Unusable:
    """The clean picture of only `cleaned_of` drawn from the shop owner's photo at `photo`,
    a cut-out checked by the cut-out check (asked once) and drawn again when it fails, as
    the clean picture of their thing on a fix step is (work.clean_picture). Gives its key, or
    why none passed, said in red to the shop owner. Run again, it pays for nothing already
    paid for."""
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
    post_notice(
        job,
        f"A clean picture couldn't be drawn from the photo you sent: {why}. It isn't in "
        "the ad's library, so it won't be sent for any scene: send a clearer photo of it.",
        Message.Level.PROBLEM,
    )
    return Unusable(why)


def say_unreadable(job: Job, unreadable: int) -> None:
    """Tell the shop owner in red of each video they sent that couldn't be read."""
    for _ in range(unreadable):
        post_notice(
            job,
            "The video you sent couldn't be read as a video, so nothing from it is in the "
            "ad's library.",
            Message.Level.PROBLEM,
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
