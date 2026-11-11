import logging
import threading
import time
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import partial
from typing import NamedTuple
from uuid import UUID, uuid4

from celery import shared_task
from celery.signals import worker_ready
from django.conf import settings
from django.db import connection, transaction
from django.db.models import Q, QuerySet
from django.utils import timezone

from adforge import monitoring, tracing
from adforge.retry import OutsideServiceDown
from chat import messages
from chat.models import Attachment, Message, Session
from gateway.gateway import asking_again, charged_to
from gateway.models import ModelCall
from gateway.types import BlockedBySafetyFilter, ClipFailed, UnusableReply
from jobs import notices, owner_answers, owner_parts, stale
from jobs.models import AgentFix, Job, ProducedItem, Scene, SceneStep
from jobs.notices import post_notice
from jobs.reference_photos import ReferencePhotoUnusable
from jobs.work import (
    agent_gave_up,
    carries_its_fix_on,
    end_the_agents_fixing,
    say_the_agent_gave_up,
    version_label,
)

from . import card_picks, loop, page_panel, planning_questions, scene_agent, step_signals
from .loop import (
    EXPECTED_FAILURES,
    Asks,
    ToolAnswer,
    why_it_failed,
    why_it_failed_plainly,
)
from .models import SystemNote
from .money import dollars
from .producer import PRODUCER, STEP_NAMES

logger = logging.getLogger(__name__)


# What they are told when the safety filter stopped its turn every time it was taken again.
FILTER_KEPT_STOPPING = (
    "The AI service's safety filter kept stopping my work on this ad. Send me a message to try "
    "again."
)
# What the producer is told as its turn the safety filter stopped is taken the last time: the
# same as the shop owner's "please carry on" did.
FILTER_RETRY_NOTE = (
    "Your last answer was stopped by the AI service's safety filter. Carry on with the ad."
)


# Never handed out again after a crash: Celery can't be relied on to, so a producer that
# died is noticed by its heartbeat stopping, and started again (see wake_producer).
@shared_task(acks_late=False)
def run_producer(session_id: str) -> None:
    """Let the producer work in the session until it replies: every message the shop owner
    sends is its to answer. A turn the safety filter stopped is taken again by itself, and if
    it can't carry on, the chat is told, plainly, and the developer record why."""
    session = Session.objects.get(pk=session_id)
    copy = _take_the_session(session)
    if copy is None:
        return
    told = loop.STOPPED

    def beat() -> None:
        # Only while it holds the session: once it stopped, or another copy took over from it,
        # a late beat mustn't make the session look worked on.
        now = timezone.now()
        Session.objects.filter(pk=session_id, producer_copy=copy).update(
            producer_seen_at=now, producer_beat_at=now
        )

    beating = _beating(
        settings.PRODUCER_HEARTBEAT_SECONDS, beat, f"The producer in session {session_id}"
    )
    try:
        with (
            beating,
            monitoring.working_on(session=session_id, ad=session.name, step="producer"),
        ):
            _work_until_it_stops(session, copy)
    except BlockedBySafetyFilter as error:
        why = f"the safety filter stopped its turn every time it was taken ({error})"
        told = FILTER_KEPT_STOPPING
    except UnusableReply as error:
        why = f"its AI model's answer couldn't be used ({error})"
    except OutsideServiceDown as error:
        why = f"the AI service stayed down after several tries ({error})"
    except Exception as error:
        logger.exception("The producer stopped in session %s", session_id)
        why = f"an unexpected error happened ({type(error).__name__}: {error})"
    else:
        return
    finally:
        tracing.flush()
    # No producer is working any more, so the next message starts one.
    with transaction.atomic():
        loop.say_it_stopped(session, told, f"The producer stopped: {why}.")
        Session.objects.filter(pk=session_id, producer_copy=copy).update(
            producer_running=False, **LET_GO
        )


# What a producer copy writes as it lets go of its session.
LET_GO = {"producer_copy": None, "producer_beat_at": None}


def _producer_dead_since() -> datetime:
    """A working producer copy not heard from since this time has died."""
    return timezone.now() - timedelta(seconds=settings.PRODUCER_DEAD_AFTER_SECONDS)


def _step_dead_since() -> datetime:
    """A scene step taken but not heard from since this time has died with its worker."""
    return timezone.now() - timedelta(seconds=settings.STEP_DEAD_AFTER_SECONDS)


def _still_holds(session_id: str, copy: UUID) -> bool:
    return Session.objects.filter(pk=session_id, producer_copy=copy).exists()


def _take_the_session(session: Session) -> UUID | None:
    """Take the session for this copy of the producer, and give the copy's name. A copy is
    sent again whenever the last one hasn't been seen for a while, and one waiting in a full
    queue isn't seen, so two copies can be sent to one session (fix triage DW-bug-1). Only one
    works: this one quits, giving None, while another working copy's heartbeat is recent, or
    once the producer has stopped since this copy was sent. The developer is told it quit."""
    copy = uuid4()
    now = timezone.now()
    taken = (
        Session.objects.filter(pk=session.pk, producer_running=True)
        .filter(Q(producer_copy__isnull=True) | Q(producer_beat_at__lt=_producer_dead_since()))
        .update(producer_copy=copy, producer_beat_at=now, producer_seen_at=now)
    )
    if taken:
        return copy
    working = Session.objects.filter(pk=session.pk, producer_running=True).exists()
    notices.record(
        session,
        notices.SECOND_COPY_QUIT,
        "A second copy of the producer quit as it started: "
        + (
            "another copy is working in the session."
            if working
            else "the producer had already stopped, with nothing left to do."
        ),
        Message.Level.INFO,
    )
    return None


def _work_until_it_stops(session: Session, copy: UUID) -> None:
    """The producer only stops once it has read everything the user sent and every scene
    step's result: a message that arrives as it replies is an interrupt, and it works on that
    too. A turn the safety filter stopped is taken again after each wait of
    PRODUCER_FILTER_RETRY_SECONDS, the last time with a note to carry on; past them, the
    filter's error is raised. Each retry is recorded. A copy that no longer holds the session
    (`copy`), as when its heartbeat stalled and another copy took over, stops before its
    next turn, and the developer is told."""
    waits = settings.PRODUCER_FILTER_RETRY_SECONDS
    retried = 0
    while True:
        if not _still_holds(str(session.pk), copy):
            notices.record(
                session,
                notices.SECOND_COPY_QUIT,
                "A producer copy stopped before its next turn: another copy took the session "
                "over after its heartbeat went unseen.",
                Message.Level.INFO,
            )
            return
        taken = _turns_taken(session)
        try:
            read_up_to = _work_on(session)
        except BlockedBySafetyFilter as error:
            # Each turn the filter stops gets its own retries: one taken since the last stop
            # starts them afresh.
            if _turns_taken(session) > taken:
                retried = 0
            if retried == len(waits):
                raise
            notices.record(
                session,
                notices.PRODUCER_RETRIED,
                f"The safety filter stopped the producer's turn ({error}), so it is taken again "
                f"in {waits[retried]:g} s (retry {retried + 1} of {len(waits)}).",
                Message.Level.INFO,
            )
            # The heartbeat beats on its own thread meanwhile, so the producer isn't taken
            # for dead.
            time.sleep(waits[retried])
            retried += 1
            if retried == len(waits):
                SystemNote.objects.create(
                    session=session, kind=SystemNote.Kind.FILTER_RETRY, text=FILTER_RETRY_NOTE
                )
            continue
        retried = 0
        if _stop_unless_theres_more_to_read(str(session.pk), read_up_to, copy):
            return


def _turns_taken(session: Session) -> int:
    """How many of the producer's turns its model answered in the session so far."""
    return ModelCall.objects.filter(
        session=session, purpose=PRODUCER.purpose, outcome=ModelCall.Outcome.SUCCEEDED
    ).count()


def _work_on(session: Session) -> int:
    """Keep any page the shop owner pasted in the page panel and assemble the ad again with any
    version they swapped in from a scene's card, then let the producer work on what they
    sent, a scene step's result, the shop owner's pause or the swap.
    Gives the number of the last message read."""
    page_panel.take_pastes(session)
    card_picks.assemble_after_swaps(session)
    return loop.run(PRODUCER, session, woken_by=_woken_by(session))


def _woken_by(session: Session) -> list[str]:
    """The scene steps that finished since the producer last read them, by name."""
    unread = loop.unread_steps(PRODUCER, session).select_related("scene")
    return [_step_name(step) for step in unread.order_by("finished_at", "id")]


def _stop_unless_theres_more_to_read(session_id: str, read_up_to: int, copy: UUID) -> bool:
    """Mark the producer stopped, unless the user said something after the messages up to
    `read_up_to` it was last given, sent a pasted page code hasn't looked at, or a scene
    step's result would wake it (see _a_result_wakes_it) or the shop owner paused and it
    hasn't been given that, or it is nudged to carry on with the ad it left unfinished (see
    _nudge). Both
    happen under the session's lock, which a message sent and a step finished take before
    they look for a producer, so each is either found here or starts a new producer itself."""
    with transaction.atomic():
        session = Session.objects.select_for_update().get(pk=session_id)
        if session.messages.filter(role=Message.Role.USER, seq__gt=read_up_to).exists():
            return False
        if page_panel.unlooked_at(session).exists():
            return False
        if _a_result_wakes_it(session):
            return False
        # The note of a pause pressed, or a version swapped in from a card, while it worked.
        if loop.unread_pause_notes(session).exists() or loop.unread_swap_notes(session).exists():
            return False
        if session.system_notes.filter(
            kind__in=[SystemNote.Kind.PLANNER_TIMEOUT, SystemNote.Kind.NOTE_WAITING],
            read_at__isnull=True,
        ).exists():
            return False
        if _nudge(session):
            return False
        # A copy another took over from leaves the session to it.
        if session.producer_copy != copy:
            return True
        # Let go of the session in the same statement, so the next copy sent can take it.
        Session.objects.filter(pk=session_id).update(producer_running=False, **LET_GO)
        return True


def _a_result_wakes_it(session: Session) -> bool:
    """Whether a scene step's result the producer hasn't been given would wake it: one
    failed, or none of the ad's steps is still running (round 7, item 13). A finished one
    while another runs waits for the last to finish, which wakes it."""
    unread = loop.unread_steps(PRODUCER, session)
    if not unread.exists():
        return False
    if unread.exclude(status=SceneStep.Status.FINISHED).exists():
        return True
    job = session.jobs.first()
    return job is None or not _running(job.pk).exists()


def _nudge(session: Session) -> bool:
    """Nudge the producer to carry on, with a system note naming what is left, when it is about
    to stop with the ad unfinished and nothing will wake it (spec #272, 2a): no scene step is
    running, nothing waits on the shop owner, and the run isn't paused or held. At most
    PRODUCER_MOST_NUDGES times per message from the shop owner. A nudge after which it called
    no tool was answered (it told the shop owner why, or waits on them): it isn't nudged
    again. Past the last nudge, the shop owner is told plainly that it stopped, unless it said
    something itself. Each nudge and stop is recorded. Gives whether it was nudged; called
    under the session's lock."""
    left = _whats_left(session)
    if left is None:
        return False
    spoke = session.messages.filter(role=Message.Role.USER).order_by("seq").last()
    since = spoke.created_at if spoke is not None else session.created_at
    if _something_will_wake_it(session, since):
        return False
    nudges = list(session.system_notes.filter(kind=SystemNote.Kind.NUDGE, created_at__gt=since))
    if (
        nudges
        and not session.tool_calls.filter(
            agent=PRODUCER.name, created_at__gt=nudges[-1].created_at
        ).exists()
    ):
        return False
    if len(nudges) >= settings.PRODUCER_MOST_NUDGES:
        if nudges:
            last = nudges[-1].created_at
            said = session.messages.filter(
                role=Message.Role.AGENT, created_at__gt=last, text__gt=""
            ).exists()
            notices.record(
                session,
                notices.PRODUCER_NUDGED,
                f"The producer stopped again after {len(nudges)} nudges with {left.for_producer} "
                "still unmade, so it is left stopped.",
                Message.Level.INFO,
            )
            if not said:
                loop.say_it_stopped(
                    session,
                    f"I stopped before {left.for_owner}. Send me any message and I'll pick it up.",
                    f"The producer stopped with {left.for_producer} unmade, after {len(nudges)} "
                    "nudges.",
                )
        return False
    SystemNote.objects.create(
        session=session,
        kind=SystemNote.Kind.NUDGE,
        text=(
            f"{left.for_producer[:1].upper()}{left.for_producer[1:]} "
            f"{'aren' if left.many else 'isn'}'t made yet, and nothing is running or waiting on "
            "the shop owner. Carry on, or tell the shop owner in one short message why you "
            "can't. If you are waiting on the shop owner's answer to something you asked, end "
            "your turn without saying anything."
        ),
    )
    notices.record(
        session,
        notices.PRODUCER_NUDGED,
        f"The producer went to stop with {left.for_producer} unmade and nothing waiting, so it "
        f"was nudged to carry on (nudge {len(nudges) + 1} of {settings.PRODUCER_MOST_NUDGES}).",
        Message.Level.INFO,
    )
    return True


class _Left(NamedTuple):
    """What of the ad isn't made yet: for the producer ("scene 4's clip"), for the shop owner
    ("scene 4 was made"), and whether it is more than one thing."""

    for_producer: str
    for_owner: str
    many: bool


def _whats_left(session: Session) -> _Left | None:
    """What of the session's ad isn't made yet, or None when nothing is: no ad started (no
    job, so nothing to carry on with), or every scene's clip made."""
    job = session.jobs.first()
    if job is None:
        return None
    scenes = list(job.scenes.order_by("number"))
    if not scenes:
        return _Left("the ad's plan", "the ad was planned", many=False)
    unmade = [scene.number for scene in scenes if scene.status != Scene.Status.FINISHED]
    if not unmade:
        return None
    if len(unmade) == 1:
        return _Left(f"scene {unmade[0]}'s clip", f"scene {unmade[0]} was made", many=False)
    numbers = ", ".join(str(number) for number in unmade[:-1]) + f" and {unmade[-1]}"
    return _Left(f"the clips of scenes {numbers}", f"scenes {numbers} were made", many=True)


def _something_will_wake_it(session: Session, since: datetime) -> bool:
    """Whether the producer has no need of a nudge: a scene step is running (its result wakes
    it), the shop owner is asked something the ad waits on (by the producer or code since
    their last message, at `since`; for their yes on an owner part's
    picture; for the product page in the page panel; or for a product photo, when the page
    read gave none), or the shop owner paused or the run is held by the step limit."""
    job = session.jobs.first()
    assert job is not None
    if SceneStep.objects.filter(scene__job=job, status=SceneStep.Status.RUNNING).exists():
        return True
    if loop.paused(session) or loop.held_by_the_step_limit(session):
        return True
    if session.tool_calls.filter(
        agent=PRODUCER.name, tool="ask_owner", created_at__gt=since
    ).exists():
        return True
    if notices.asked_since(job, since) or messages.page_panel_wanted(session):
        return True
    if job.page_text and not job.photos.filter(from_video=None).exists():
        return True
    return any(
        owner_parts.owner_part(scene) is not None
        and not owner_parts.is_approved(scene)
        and owner_parts.picture_is_current(scene)
        for scene in job.scenes.exclude(status=Scene.Status.FINISHED)
    )


@contextmanager
def _beating(every_seconds: float, beat: Callable[[], object], what: str) -> Iterator[None]:
    """Say something is still alive by calling `beat` every `every_seconds`, for as long as
    the block runs. Beats on its own thread, so on its own database connection, which it
    closes."""
    stop = threading.Event()

    def beating() -> None:
        try:
            while not stop.wait(every_seconds):
                # One beat that fails mustn't stop the rest: what stops beating is taken for
                # dead while it still works.
                try:
                    beat()
                except Exception:
                    logger.exception("%s's heartbeat failed", what)
                    connection.close()
        finally:
            connection.close()

    thread = threading.Thread(target=beating, daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join()


def wake_producer(session_id: str) -> None:
    """Start the producer in the session, unless one is already working there. A working
    producer reads what the user sent when its model call returns, so it isn't started
    twice. A copy working is judged by its own heartbeat: one that went silent has died, and
    is started again. A copy sent but not started yet may wait in a full queue for a while,
    so it is only sent again once PRODUCER_UNSTARTED_AFTER_SECONDS pass, in case it was lost
    (fix triage DW-bug-1)."""
    with transaction.atomic():
        session = Session.objects.select_for_update().get(pk=session_id)
        if session.producer_running and _a_copy_is_on_its_way(session):
            return
        session.producer_running = True
        session.producer_seen_at = timezone.now()
        session.save(update_fields=["producer_running", "producer_seen_at"])
        transaction.on_commit(lambda: run_producer.delay(session_id))


def _a_copy_is_on_its_way(session: Session) -> bool:
    """Whether a producer copy works in the session, or was sent recently enough that it
    may still be waiting in the queue."""
    if session.producer_copy is not None:
        beat = session.producer_beat_at
        return beat is not None and beat > _producer_dead_since()
    unstarted_after = timedelta(seconds=settings.PRODUCER_UNSTARTED_AFTER_SECONDS)
    sent = session.producer_seen_at
    return sent is not None and sent > timezone.now() - unstarted_after


# Which Postgres advisory lock stands for "one session works at a time". Any number will
# do, as long as nothing else takes the same one.
ONE_AT_A_TIME_LOCK = 1


def hold_one_at_a_time_lock() -> None:
    """Wait for, then hold until the transaction ends, the lock every message sent takes
    before it looks for another busy session. Two messages sent to two sessions at the same
    moment then look one after the other, so they can't both start a producer."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(%s)", [ONE_AT_A_TIME_LOCK])


def another_session_is_busy(session: Session) -> bool:
    """Whether a session other than `session` is working, so `session` mustn't start.
    A producer marked running counts however long ago it was seen: one that died is
    always started again, so it works or soon will. Nothing starts a scene step again, so a
    running step only counts while its heartbeat is recent. Never, with ONE_AD_AT_A_TIME
    off, as for a run of several ads at once."""
    if not settings.ONE_AD_AT_A_TIME:
        return False
    others = Session.objects.exclude(pk=session.pk)
    if others.filter(producer_running=True).exists():
        return True
    return (
        SceneStep.objects.filter(status=SceneStep.Status.RUNNING, seen_at__gt=_step_dead_since())
        .exclude(tool_call__session=session)
        .exists()
    )


def _told_it_waits(step: SceneStep) -> str:
    """What the producer is told when the chat has just told the shop owner which scenes wait
    for their yes, as when this picture was the last thing being made."""
    return "".join(
        f"The chat told the shop owner that scene {scene.number} waits for their yes on "
        f"the picture of {part.what}, so the ad can't be assembled yet: don't tell them "
        "again. "
        for scene, part in say_what_waits_on_the_shop_owner(step.scene.job, step)
    )


def _needs_a_yes(step: SceneStep) -> str:
    """What the producer is told about a starting picture of an owner part, or blank for a
    picture of none: the shop owner's yes is on the part's own picture, never on this one. A
    part they already said yes to is settled: they aren't asked about it again."""
    if step.owner_part is None:
        return ""
    part = owner_parts.OwnerPart.model_validate(step.owner_part)
    if owner_parts.is_approved(step.scene):
        return (
            f"The picture shows {part.what}, which the shop owner already said yes to: don't "
            "ask them about it again. "
        )
    return (
        f"The picture shows {part.what}, which no product photo shows. The clip waits for the "
        f"shop owner's yes on the picture of {part.what} itself, shown to them in the chat, "
        "not on this one: don't ask them about this picture. "
    )


def _cost(step: SceneStep) -> str:
    """What the step's work cost, the fixing agent's work on it included, as a sentence to
    follow another."""
    return f" It cost {dollars(step.cost_usd())}."


def _made_for_the_scene_before(number: int) -> str:
    """What the producer is told of a clip step's clip made for its scene as it was before a
    plan edit."""
    return (
        f"It was made for scene {number} as it was before the plan changed, so it is stale "
        f"and scene {number} isn't finished: make its stale steps again if the scene needs them."
    )


def say_what_waits_on_the_shop_owner(
    job: Job, finishing: SceneStep | None = None
) -> list[tuple[Scene, owner_parts.OwnerPart]]:
    """Once every scene is finished but those waiting for the shop owner's yes on their owner
    part, tell the shop owner in the chat which scenes wait and why, once each, ever. Gives
    the scenes told of now, each with its part. `finishing` is the step whose end is being
    told: it counts as done."""
    running = SceneStep.objects.filter(status=SceneStep.Status.RUNNING)
    if finishing is not None:
        running = running.exclude(pk=finishing.pk)
    scenes = list(job.scenes.order_by("number"))
    waiting = [
        (scene, part)
        for scene in scenes
        if scene.status != Scene.Status.FINISHED
        and (part := owner_parts.owner_part(scene)) is not None
        and not owner_parts.is_approved(scene)
        and owner_parts.picture_is_current(scene)
        and not running.filter(scene=scene).exists()
    ]
    if len(waiting) + sum(scene.status == Scene.Status.FINISHED for scene in scenes) != len(scenes):
        return []
    # Read again: a notice posted since the job was read is in the stored warnings only.
    told = {warning["text"] for warning in Job.objects.get(pk=job.pk).warnings}
    newly = [(scene, part) for scene, part in waiting if _waits(scene, part) not in told]
    for scene, part in newly:
        post_notice(job, notices.PART_NEEDS_YES, _waits(scene, part), Message.Level.INFO)
    return newly


def _waits(scene: Scene, part: owner_parts.OwnerPart) -> str:
    """What the shop owner is told when the rest of the ad is made and `scene` waits."""
    drawn = owner_parts.drawn_by(part)
    return (
        f"Scene {scene.number} waits for your yes on the picture of {part.what}: no photo "
        f"showed it, so it was drawn {drawn}. Say yes if it looks right, or tell me what "
        "to change, or send a photo of it. Every scene that isn't waiting on you is made, and "
        "the ad is put together once this scene's clip is made."
    )


@dataclass(frozen=True)
class StepWork:
    """What one kind of scene step does: the work itself, what the producer is told when it
    finishes, and what the chat shows then."""

    # Gives what it made.
    make: Callable[[SceneStep], ProducedItem | None]
    finished: Callable[[SceneStep, ProducedItem | None], str]
    shown: Callable[[SceneStep, ProducedItem | None], list[messages.AttachedFile]]


def _retired_step(step: SceneStep) -> ProducedItem | None:
    """Old producer steps cannot submit work after their provider path was retired."""
    raise ClipFailed(
        "this producer scene step was retired; start its scene agent to make a new clip"
    )


RETIRED_STEP = StepWork(
    make=_retired_step,
    finished=lambda _step, _made: "",
    shown=lambda _step, _made: [],
)


STEP_WORK = {
    SceneStep.Kind.SCENE_AGENT: StepWork(
        make=scene_agent.run,
        finished=lambda step, made: (
            f"{scene_agent.finished(step, made)} {_told_it_waits(step)}".rstrip()
        ),
        shown=lambda step, clip: (
            [
                messages.AttachedFile(
                    Attachment.Kind.VIDEO,
                    clip.file,
                    step.scene.number,
                    version_label(clip),
                    clip=clip,
                )
            ]
            if clip and step.agent_fix.passed_id == clip.pk
            else []
        ),
    ),
}


def _step_name(step: SceneStep) -> str:
    """Such as "scene 1's starting picture"."""
    return f"scene {step.scene.number}'s {STEP_NAMES[SceneStep.Kind(step.kind)]}"


def _scene_agent_children() -> QuerySet[SceneStep]:
    """Render and Scribe checkpoints run inside their scene-agent parent.

    Early H3 workers could leave these children marked running. Their parent resumes
    the paid work; dispatching a child as a producer step would retire it by mistake.
    """
    return SceneStep.objects.filter(
        kind__in=[SceneStep.Kind.CLIP, SceneStep.Kind.TRANSCRIPT],
        tool_call__agent_fix__step__kind=SceneStep.Kind.SCENE_AGENT,
    )


@shared_task
def run_scene_step(step_id: int) -> None:
    """Do a scene step's work in the background, then have the producer told: it reads the
    result on its next turn, started if it isn't working once the step failed or no other
    step of the ad runs (see _finish)."""
    step = SceneStep.objects.select_related("scene__job", "tool_call__session").get(pk=step_id)
    if _scene_agent_children().filter(pk=step.pk).exists():
        SceneStep.objects.filter(pk=step.pk, status=SceneStep.Status.RUNNING).update(
            status=SceneStep.Status.FINISHED
        )
        return
    # Celery may hand a step out again after a crash. One that has finished or failed since
    # is left as it is, so nothing is shown or paid for twice; so is one the shop owner's
    # pause cancelled before it was taken.
    if step.status != SceneStep.Status.RUNNING:
        return
    # Taken: from now on its heartbeat says whether it is alive (see fail_dead_steps). Only
    # while it is still running, in one statement, so a pause cancelling it at the same
    # moment either cancels it first, and it is left, or finds it taken, and keeps it.
    # Only if no copy that is still alive has taken it: the broker hands out again a task
    # still unfinished after its visibility timeout, and that second copy would pay for
    # everything again beside the first (fix triage DW1). Every step sent again on purpose
    # is untaken first; one whose copy stopped beating died with its worker.
    now = timezone.now()
    if not SceneStep.objects.filter(
        Q(taken_at__isnull=True) | Q(seen_at__lt=_step_dead_since()),
        pk=step_id,
        status=SceneStep.Status.RUNNING,
    ).update(taken_at=now, seen_at=now):
        taken_at = (
            SceneStep.objects.filter(pk=step_id, status=SceneStep.Status.RUNNING)
            .values_list("taken_at", flat=True)
            .first()
        )
        if taken_at is not None:
            post_notice(
                step.scene.job,
                notices.SECOND_COPY_QUIT,
                f"A second copy of {_step_name(step)} quit as it started: another copy took "
                f"it at {taken_at.isoformat()} and is still working on it.",
                Message.Level.INFO,
                scene=step.scene.number,
            )
        return
    step.taken_at = now
    session = step.tool_call.session
    work = STEP_WORK.get(SceneStep.Kind(step.kind), RETIRED_STEP)
    # Every failure sent to Sentry while it works names its scene and step.
    with monitoring.working_on(
        session=str(session.pk), ad=session.name, scene=step.scene.number, step=step.kind
    ):
        _do_the_work(step, session, work)


def _do_the_work(step: SceneStep, session: Session, work: StepWork) -> None:
    """A taken scene step's work, then its finish, or its failure or question, told."""
    step_id = step.pk
    try:
        # Its trace part is open until the step is finished, so it shows what was handed back.
        with tracing.scene_step(
            kind=step.kind,
            scene=step.scene.number,
            session_id=str(session.pk),
            ad=loop.traced_ad(session, step.scene.job),
            trace_id=step.tool_call.trace_id,
            parent_id=step.tool_call.observation_id,
        ) as traced:
            # Before the work, so a step that fails still shows what it was asked.
            _asked(traced, step, prompt="")
            with (
                _beating(
                    settings.STEP_HEARTBEAT_SECONDS,
                    # Only while this copy holds it, so a copy another took over from can't
                    # keep it looking alive.
                    lambda: SceneStep.objects.filter(pk=step_id, taken_at=step.taken_at).update(
                        seen_at=timezone.now()
                    ),
                    f"Scene step {step_id}",
                ),
                charged_to(step.tool_call),
                # A step's models are never asked again, even when the step runs inside the
                # producer's tool call that started it, as with a worker in the same process.
                asking_again(False),
            ):
                made = work.make(step)
            # Again once the work has written the prompt its model was given.
            _asked(traced, step, prompt=_prompt_given(step))
            _finish(step, work, made, session, traced)
        tracing.flush()
    except Asks as asks:
        # The step's work needs one answer before it can make anything: its question goes
        # back to whoever started it as the step's answer, not as a failure (spec #247 point 6).
        tracing.flush()
        with transaction.atomic():
            if not _still_running(step):
                return
            _ask_back(step, str(asks))
            _told_it_ended(step)
    # Whatever stops the step, it isn't left running: the producer is told why.
    except Exception as error:
        if not isinstance(error, EXPECTED_FAILURES):
            logger.exception("Scene step %s failed", step_id)
        tracing.flush()
        with transaction.atomic():
            if not _still_running(step):
                return
            _fail(
                step,
                why_it_failed(error, "the step"),
                why_it_failed_plainly(error),
                owner_can_help=isinstance(error, ReferencePhotoUnusable),
            )
            _told_it_ended(step)


def _asked(traced: tracing.Traced, step: SceneStep, *, prompt: str) -> None:
    """Record on a scene step's trace what it was asked, with the prompt its model was given."""
    tracing.step_asked(
        traced,
        kind=step.kind,
        scene=step.scene.number,
        line=step.line,
        note=step.note,
        prompt=prompt,
    )


def _prompt_given(step: SceneStep) -> str:
    """The prompt the scene step's work last sent a model, word for word, as its record keeps
    it: such as a starting picture's or a clip's. Blank for a step whose models are sent
    none, as a line's audio."""
    sent = (
        ModelCall.objects.filter(
            tool_call=step.tool_call,
            created_at__gte=step.taken_at,
            outcome=ModelCall.Outcome.SUCCEEDED,
            handoff__has_key="prompt",
        )
        .values_list("handoff", flat=True)
        .last()
    )
    return str(sent["prompt"]) if isinstance(sent, dict) else ""


def _finish(
    step: SceneStep,
    work: StepWork,
    made: ProducedItem | None,
    session: Session,
    traced: tracing.Traced,
) -> None:
    """Show what a scene step made and finish it, then tell whoever started it."""
    # Shown and finished together, so a step run again never shows anything twice. The
    # pending note is started only after that result has committed.
    with transaction.atomic():
        # The job, then the chat, as every writer of a plan locks them: a notice said
        # here locks the job after the chat's message, so the job is locked first.
        Job.objects.select_for_update().get(pk=step.scene.job_id)
        # Failed as dead while it worked, as when its heartbeat stopped for a while: the
        # producer was told then, and its scene may be made again, so it is left failed.
        if not _still_running(step):
            return
        shown = work.shown(step, made)
        if shown:
            messages.add(
                session,
                role=Message.Role.AGENT,
                carrying=shown,
                step=step_signals.signal(step, Message.StepState.MADE),
            )
        step.status = SceneStep.Status.FINISHED
        # Inside the transaction: a picture's message plans its scene again.
        step.result = work.finished(step, made)
        step.finished_at = timezone.now()
        step.save(update_fields=["status", "result", "finished_at"])
        tracing.handed_back(traced, step.result)
        # Once its step is finished, so its tries are versions.
        if step.kind == SceneStep.Kind.CLIP and step.shows and agent_gave_up(step):
            say_the_agent_gave_up(step)
        # A photo or video the shop owner answered the fixing agent's question with as its
        # fix ended, before the fix took it: the step runs again for it.
        if step.kind in (SceneStep.Kind.CLIP, SceneStep.Kind.SCENE_AGENT):
            owner_answers.run_with_a_late_answer(step)
    # A note's refused start must never roll back the clip and FINISHED above.
    from .scene_tools import start_pending_note

    start_pending_note(step)
    with transaction.atomic():
        Job.objects.select_for_update().get(pk=step.scene.job_id)
        # Once every result and pending-note outcome is saved, wake the producer only
        # when no other scene is still working.
        if not _others_running(step):
            wake_producer(str(session.pk))


def _others_running(step: SceneStep) -> bool:
    """Whether a scene step of the step's ad other than it is still running."""
    return _running(step.scene.job_id).exclude(pk=step.pk).exists()


def _running(job_id: UUID) -> QuerySet[SceneStep]:
    """The scene steps of the ad `job_id` still running: the one wake rule (round 7, item 13)
    reads them both where a step finishes and where the producer stops."""
    return SceneStep.objects.filter(scene__job_id=job_id, status=SceneStep.Status.RUNNING)


def _still_running(step: SceneStep) -> bool:
    """Whether the step is still running, its row locked until the transaction ends, so it
    can't be failed as dead while it finishes, nor finished once it was."""
    return (
        SceneStep.objects.select_for_update()
        .filter(pk=step.pk, status=SceneStep.Status.RUNNING)
        .exists()
    )


def _told_it_ended(step: SceneStep) -> None:
    """Tell the producer, which started it, that a step failed or stopped: it is woken to read
    it."""
    wake_producer(str(step.tool_call.session_id))


# The producer's tool that makes each kind of scene step.


def _fail(
    step: SceneStep,
    reason: str,
    plainly: str | None,
    ways_on: str = "",
    *,
    owner_can_help: bool = False,
) -> None:
    """End the step failed for `reason` with what the producer is told as a tool's can't is
    (spec #247 point 8): why, then its ways on from here, `ways_on`, and those of a clip the
    step kept before it ended; and record that nothing was made and why, `plainly`, unless it
    was already (None): the shop owner is told only when they `owner_can_help`
    (step_signals.say_not_made). The run never stops on it."""
    step.status = SceneStep.Status.FAILED
    step.reason = reason
    step.result = (
        f"Background step failed: {_step_name(step)} couldn't be made: {reason} "
        f"{ways_on}{_kept_clips_ways_on(step)}Take a way on; tell the shop owner only if they "
        "can help."
    )
    step.finished_at = timezone.now()
    step.save(update_fields=["status", "reason", "result", "finished_at"])
    if plainly is not None:
        step_signals.say_not_made(step, plainly, owner_can_help=owner_can_help)


def _kept_clips_ways_on(step: SceneStep) -> str:
    """For a clip step that kept its clip before it ended, with no fixing agent on it (as when
    the worker stopped while the judge looked at it): that it can be kept or made again, as a
    sentence to follow another. Blank otherwise."""
    if step.kind != SceneStep.Kind.CLIP or AgentFix.objects.filter(step=step).exists():
        return ""
    clip = step.produced.filter(kind=ProducedItem.Kind.CLIP).order_by("id").first()
    if clip is None:
        return ""
    number = step.scene.number
    return (
        f"Its clip (version {clip.version}) was made before the step ended, but scene {number} "
        f"isn't finished with it: keep it with keep_try for scene {number}, try 0, or make a "
        "new one with start_scene_agent. "
    )


def _ended_partway_ways_on(step: SceneStep) -> str:
    """The ways on from a step whose work stopped partway and can't be carried on, as a dead
    worker's (spec #247 point 8): start it again, or skip it, as a sentence to follow another."""
    number = step.scene.number
    # Only a clip made for the scene as it stands can be kept: a stale one can't be assembled.
    current = stale.current_clip(Scene.objects.get(pk=step.scene_id))
    skip = (
        f"scene {number} keeps the clip it has"
        if current is not None
        else f"remove scene {number} with remove_scene, and the ad is made without it"
    )
    return f"Start it again with start_scene_agent for scene {number}, or skip it: {skip}. "


def _end_keeping_the_clip(step: SceneStep, reason: str) -> None:
    """End a clip step run again to carry its fix on (`carries_its_fix_on`), whose work
    stopped partway for `reason`: it had finished with its clip in the ad, and that clip, or
    the last try the fix filmed since, stays there, the step finished with it as a give-up
    leaves it. Never failed, so the scene keeps a clip and nothing is made again. Recorded in
    red."""
    number = step.scene.number
    kept = AgentFix.objects.select_related("kept").get(step=step).kept
    assert kept is not None, "a fix carried on had a clip in the ad, and its give-up keeps one"
    step.status = SceneStep.Status.FINISHED
    step.result = (
        f"Background step finished: scene {number}'s fix couldn't carry on: {reason} The clip "
        f"that was in the ad (version {kept.version}) stays in the ad, and scene {number} is "
        "finished with it, so nothing needs making again. Tell the shop owner the fix couldn't "
        "carry on."
    )
    step.finished_at = timezone.now()
    step.save(update_fields=["status", "result", "finished_at"])
    stopped = (
        " with your answer: its work stopped partway again after it was started again"
        if owner_answers.resuming(step)
        else ": its work stopped partway after it was carried on"
    )
    # For the developer (spec #272): the shop owner's ad is as it was. The scene's card is told
    # the step ended with its clip.
    ended = messages.StepSignal(number, Message.StepKind.CLIP, Message.StepState.MADE)
    post_notice(
        step.scene.job,
        notices.FIX_NOT_CARRIED_ON,
        f"Scene {number}'s fix couldn't carry on{stopped}, so the clip that was in the ad stays "
        "there.",
        Message.Level.PROBLEM,
        step=ended,
        scene=number,
    )
    messages.add(
        step.tool_call.session, role=Message.Role.NOTICE, level=Message.Level.INFO, step=ended
    )


def _ask_back(step: SceneStep, question: str) -> None:
    """End the step with nothing made, its work having asked `question`: the producer is
    given the question as the step's answer, and the chat shows the step waits on it."""
    step.status = SceneStep.Status.FAILED
    step.reason = f"it asked: {question}"
    step.result = (
        f"Background step asked before making anything: {_step_name(step)} wasn't made. "
        f"{ToolAnswer.question(question).text}"
    )
    step.finished_at = timezone.now()
    step.save(update_fields=["status", "reason", "result", "finished_at"])
    step_signals.say_waits_on_a_question(step)


@shared_task
def restart_dead_producers() -> None:
    """Start the producer again in every session where it died, so the session carries on
    without the user having to send anything. Run every minute."""
    for session_id in Session.objects.filter(producer_running=True).values_list("pk", flat=True):
        wake_producer(str(session_id))


def _end_stopped(
    steps: Iterable[SceneStep], reason: str, tell: Callable[[SceneStep], None] | None = None
) -> list[SceneStep]:
    """Fail each of `steps` whose work stopped for `reason`, as the fixing agent's give-up
    leaves its scene, tell the chat, and `tell` whoever started it, together. Each on its
    own: one whose fixing can't be ended is still failed, with a red notice, so it never
    stays running; the rest are ended either way. Gives the steps failed."""
    stopped = []
    for step in steps:
        try:
            with transaction.atomic():
                if not _still_running(step):
                    continue
                # A step run again with the shop owner's answer had finished, its clip in
                # the ad: it runs again from its checkpoints, failed only past the limit.
                if owner_answers.resuming(step) and owner_answers.run_again(step):
                    continue
                # Before the step ends, so its scene is as the fixing agent's give-up
                # leaves it.
                end_the_agents_fixing(step)
                if carries_its_fix_on(step):
                    _end_keeping_the_clip(step, reason)
                else:
                    _fail(step, reason, "it was stopped partway.", _ended_partway_ways_on(step))
                if tell is not None:
                    tell(step)
        except Exception:
            logger.exception("Scene step %s couldn't be ended", step.pk)
            try:
                with transaction.atomic():
                    if not _still_running(step):
                        continue
                    _fail(step, reason, "it was stopped partway.", _ended_partway_ways_on(step))
                    post_notice(
                        step.scene.job,
                        notices.STEP_NOT_WRAPPED_UP,
                        f"{_step_name(step).capitalize()} stopped partway, and the fixing "
                        "agent's tries on it couldn't be wrapped up. Make it again.",
                        Message.Level.PROBLEM,
                        scene=step.scene.number,
                    )
                    if tell is not None:
                        tell(step)
            except Exception:
                logger.exception("Scene step %s couldn't be failed either", step.pk)
                continue
        stopped.append(step)
    return stopped


@shared_task
def fail_dead_steps() -> None:
    """Fail every scene step whose worker was killed: one taken, whose heartbeat hasn't been
    seen for STEP_FAILED_AFTER_SECONDS. Nothing else ever ends it, and while it runs its
    scene can't be made again (round 6). Its producer is told, as for any failed step, in
    the same transaction. Run every minute."""
    dead_since = timezone.now() - timedelta(seconds=settings.STEP_FAILED_AFTER_SECONDS)
    dead = (
        SceneStep.objects.filter(
            status=SceneStep.Status.RUNNING, taken_at__isnull=False, seen_at__lt=dead_since
        )
        .exclude(pk__in=_scene_agent_children().values("pk"))
        .select_related("scene", "tool_call")
    )
    _end_stopped(
        dead, "Its work stopped partway, and it was never heard from again.", _told_it_ended
    )


@shared_task
def expire_planner_questions() -> None:
    """Apply overdue planning defaults, including rounds kept across worker restarts."""
    planning_questions.expire_waiting_questions()


def carry_on_after_the_worker_starts() -> None:
    """Deal with whatever the worker was doing when it stopped. There is one worker, so as
    it starts nothing from before can still be running. Each scene step still marked running
    is failed, and its producer told: running it again could run it twice at once, as
    Celery may hand it out again too. Each producer that should be working is started again
    straight away rather than once its heartbeat has been missed, unless another session
    started while the worker was down."""
    with transaction.atomic():
        hold_one_at_a_time_lock()
        sessions = set(Session.objects.filter(producer_running=True).values_list("pk", flat=True))
        _scene_agent_children().filter(status=SceneStep.Status.RUNNING).update(
            status=SceneStep.Status.FINISHED
        )
        resumable = SceneStep.objects.filter(
            status=SceneStep.Status.RUNNING, kind=SceneStep.Kind.SCENE_AGENT
        )
        for step_id in resumable.values_list("pk", flat=True):
            resumable.filter(pk=step_id).update(taken_at=None, seen_at=timezone.now())
            transaction.on_commit(partial(run_scene_step.delay, step_id))
        stopped = _end_stopped(
            SceneStep.objects.filter(status=SceneStep.Status.RUNNING)
            .exclude(kind=SceneStep.Kind.SCENE_AGENT)
            .select_related("scene", "tool_call"),
            "The worker restarted while it was being made.",
        )
        sessions.update(step.tool_call.session_id for step in stopped)
        for session in Session.objects.filter(pk__in=sessions):
            if another_session_is_busy(session):
                continue
            # Not seen since the worker stopped, so wake_producer starts it again, and no
            # copy from before holds it.
            Session.objects.filter(pk=session.pk).update(producer_seen_at=None, **LET_GO)
            wake_producer(str(session.pk))

    expire_planner_questions()


@worker_ready.connect
def _when_the_worker_starts(**_: object) -> None:
    carry_on_after_the_worker_starts()
    connection.close()
