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

from collections.abc import Sequence
from typing import Any

from django.db import transaction
from django.utils import timezone

from chat import messages
from chat.models import Message
from gateway.types import Image

from . import library_items, product_videos, watcher
from .library import LibraryEntry, LookFrame, ProductOnly, library_of, watch_of
from .models import Job, OwnerQuestion, ProductVideo, SceneStep
from .notices import post_notice


class NotWaiting(Exception):
    """The question takes no answer now, saying why."""


def answer(
    question: OwnerQuestion,
    *,
    words: str,
    photos: Sequence[str] = (),
    videos: Sequence[str] = (),
) -> SceneStep:
    """Hand the shop owner's answer to `question` to its parked fix: their `words`, and the
    keys in the file store of the `photos` and `videos` they sent with them. The fix's clip
    step runs again in a worker, which puts the files in the library and resumes the fix with
    the answer; the chat says the scene's fix carries on, which clears the question from the
    scene's card. Gives the step. Raises NotWaiting when the question was answered already
    or withdrawn, or its fix is carrying on with an answer now; ValueError for an answer with
    nothing in it."""
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
        found.answer = {"words": words, "photos": list(photos), "videos": list(videos)}
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
    shows = f"what the shop owner sent in answer to this question: {question.question}"
    said: list[str] = []
    added: list[LibraryEntry] = []
    for key in given["photos"]:
        item = library_items.add_owner_photo(job, key, key, shows)
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
    said += _videos(job, given["videos"], added)
    text = "\n".join(
        [
            f'The shop owner answered: "{given["words"]}"'
            if given["words"]
            else "The shop owner wrote no words.",
            *(said or ["The shop owner sent no file."]),
            "This is their answer, whether or not it is what you asked for: go on with it.",
        ]
    )
    pictures = [Image(f"Library item {item.number}: {item.label}", item.file) for item in added]
    # The fixing agent's module runs the job's work, so it is imported where it is used.
    from agents import fixing_agent
    from agents.loop import ToolResult

    with transaction.atomic():
        if added:
            post_notice(
                job,
                f"What you sent is in the ad's picture library as {_items(added)}, and the "
                f"fixing agent carries on with scene {step.scene.number}'s clip.",
                Message.Level.INFO,
            )
        fixing_agent.answer_question(question, ToolResult(text, pictures))


def _videos(job: Job, keys: Sequence[str], added: list[LibraryEntry]) -> list[str]:
    """Keep and watch the videos the shop owner sent, adding their look frames to `added`.
    Gives what the agent is told of each."""
    kept: list[ProductVideo] = []
    said: list[str] = []
    for key in keys:
        video = product_videos.keep_owner_video(job, key)
        if video is None:
            post_notice(
                job,
                "The video you sent couldn't be read as a video, so nothing from it is in the "
                "ad's library.",
                Message.Level.PROBLEM,
            )
            said.append("Their video couldn't be read, so nothing from it is in the library.")
        else:
            kept.append(video)
    if kept:
        # Watches every video not watched yet: the owner's, and any page video a read missed.
        watcher.watch_videos(job, job.page_text)
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
    return said


def _items(items: Sequence[LibraryEntry]) -> str:
    """Such as "item 9", or "items 9 and 10"."""
    numbers = [str(item.number) for item in items]
    if len(numbers) == 1:
        return f"item {numbers[0]}"
    return f"items {', '.join(numbers[:-1])} and {numbers[-1]}"
