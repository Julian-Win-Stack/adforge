import logging
import threading
import time
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import timedelta

from celery import shared_task
from celery.signals import worker_ready
from django.conf import settings
from django.db import connection, transaction
from django.utils import timezone

from adforge import monitoring, tracing
from adforge.retry import OutsideServiceDown
from chat import messages
from chat.models import Attachment, Message, Session
from gateway.gateway import charged_to
from gateway.models import ModelCall
from gateway.types import BlockedBySafetyFilter, UnusableReply
from jobs import notices, owner_answers, owner_parts, stale
from jobs.models import AgentFix, Job, OwnerQuestion, ProducedItem, Scene, SceneStep
from jobs.notices import post_notice
from jobs.reference_photos import ReferencePhotoUnusable
from jobs.work import (
    agent_fix,
    agent_gave_up,
    clip_tries,
    end_the_agents_fixing,
    is_parked,
    make_clip,
    make_line_audio,
    make_starting_picture,
    say_the_agent_gave_up,
    too_long_for_a_clip,
    transcribe_line_audio,
    version_label,
    what_the_fix_did,
    what_the_judge_did,
)

from . import loop, page_panel, step_signals
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


# Never handed out again after a crash: Celery can't be relied on to, so a producer that
# died is noticed by its heartbeat stopping, and started again (see wake_producer).
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
    told = loop.STOPPED
    beating = _beating(
        settings.PRODUCER_HEARTBEAT_SECONDS,
        lambda: Session.objects.filter(pk=session_id).update(producer_seen_at=timezone.now()),
        f"The producer in session {session_id}",
    )
    try:
        with (
            beating,
            monitoring.working_on(session=session_id, ad=session.name, step="producer"),
        ):
            _work_until_it_stops(session)
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
        Session.objects.filter(pk=session_id).update(producer_running=False)


def _work_until_it_stops(session: Session) -> None:
    """The producer only stops once it has read everything the user sent and every scene
    step's result: a message that arrives as it replies is an interrupt, and it works on that
    too. A turn the safety filter stopped is taken again after each wait of
    PRODUCER_FILTER_RETRY_SECONDS, the last time with a note to carry on; past them, the
    filter's error is raised. Each retry is recorded."""
    waits = settings.PRODUCER_FILTER_RETRY_SECONDS
    retried = 0
    while True:
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
            _record(
                session,
                notices.PRODUCER_RETRIED,
                f"The safety filter stopped the producer's turn ({error}), so it is taken again "
                f"in {waits[retried]:g} s (retry {retried + 1} of {len(waits)}).",
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
        if _stop_unless_theres_more_to_read(str(session.pk), read_up_to):
            return


def _turns_taken(session: Session) -> int:
    """How many of the producer's turns its model answered in the session so far."""
    return ModelCall.objects.filter(
        session=session, purpose=PRODUCER.purpose, outcome=ModelCall.Outcome.SUCCEEDED
    ).count()


def _record(session: Session, kind: notices.Kind, text: str) -> None:
    """Record `text`, a notice of `kind` for the developer, with the session's job, or only
    in Sentry before it has one."""
    job = session.jobs.first()
    if job is None:
        monitoring.failed(kind.name, text, session=str(session.pk))
    else:
        post_notice(job, kind, text, Message.Level.INFO)


def _work_on(session: Session) -> int:
    """Keep any page the shop owner pasted in the page panel, then let the producer work on
    what they sent, a scene step's result or the shop owner's pause.
    Gives the number of the last message read."""
    page_panel.take_pastes(session)
    return loop.run(PRODUCER, session, woken_by=_woken_by(session))


def _woken_by(session: Session) -> list[str]:
    """The scene steps that finished since the producer last read them, by name."""
    unread = loop.unread_steps(PRODUCER, session).select_related("scene")
    return [_step_name(step) for step in unread.order_by("finished_at", "id")]


def _stop_unless_theres_more_to_read(session_id: str, read_up_to: int) -> bool:
    """Mark the producer stopped, unless the user said something after the messages up to
    `read_up_to` it was last given, sent a pasted page code
    hasn't looked at, or a scene step finished or the shop owner paused and it hasn't been
    given that, or it is nudged to carry on with the ad it left unfinished (see _nudge). Both
    happen under the session's lock, which a message sent and a step finished take before
    they look for a producer, so each is either found here or starts a new producer itself."""
    with transaction.atomic():
        session = Session.objects.select_for_update().get(pk=session_id)
        if session.messages.filter(role=Message.Role.USER, seq__gt=read_up_to).exists():
            return False
        if page_panel.unlooked_at(session).exists():
            return False
        if loop.unread_steps(PRODUCER, session).exists():
            return False
        # The note of a pause pressed while it worked.
        if loop.unread_pause_notes(session).exists():
            return False
        if _nudge(session):
            return False
        session.producer_running = False
        session.save(update_fields=["producer_running"])
        return True


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
    if left is None or _something_will_wake_it(session):
        return False
    spoke = session.messages.filter(role=Message.Role.USER).order_by("seq").last()
    since = spoke.created_at if spoke is not None else session.created_at
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
            _record(
                session,
                notices.PRODUCER_NUDGED,
                f"The producer stopped again after {len(nudges)} nudges with {left[0]} still "
                "unmade, so it is left stopped.",
            )
            if not said:
                loop.say_it_stopped(
                    session,
                    f"I stopped before {left[1]}. Send me any message and I'll pick it up.",
                    f"The producer stopped with {left[0]} unmade, after {len(nudges)} nudges.",
                )
        return False
    SystemNote.objects.create(
        session=session,
        kind=SystemNote.Kind.NUDGE,
        text=(
            f"{left[0][:1].upper()}{left[0][1:]} isn't made yet, and nothing is running or "
            "waiting on the shop owner. Carry on, or tell the shop owner in one short message "
            "why you can't. If you are waiting on the shop owner's answer to something you "
            "asked, end your turn without saying anything."
        ),
    )
    _record(
        session,
        notices.PRODUCER_NUDGED,
        f"The producer went to stop with {left[0]} unmade and nothing waiting, so it was "
        f"nudged to carry on (nudge {len(nudges) + 1} of {settings.PRODUCER_MOST_NUDGES}).",
    )
    return True


def _whats_left(session: Session) -> tuple[str, str] | None:
    """What of the session's ad isn't made yet, for the producer ("scene 4's clip") and for
    the shop owner ("scene 4 was made"), or None when nothing is: no ad started (no job, so
    nothing to carry on with), or every scene's clip made."""
    job = session.jobs.first()
    if job is None:
        return None
    scenes = list(job.scenes.order_by("number"))
    if not scenes:
        return "the ad's plan", "the ad was planned"
    unmade = [scene.number for scene in scenes if scene.status != Scene.Status.FINISHED]
    if not unmade:
        return None
    if len(unmade) == 1:
        return f"scene {unmade[0]}'s clip", f"scene {unmade[0]} was made"
    numbers = ", ".join(str(number) for number in unmade[:-1]) + f" and {unmade[-1]}"
    return f"the clips of scenes {numbers}", f"scenes {numbers} were made"


def _something_will_wake_it(session: Session) -> bool:
    """Whether the producer has no need of a nudge: a scene step is running (its result wakes
    it), the shop owner is asked something (by the producer since their last message, by the
    fixing agent, for their yes on an owner part's picture, for the product page in the page
    panel, or for a product photo, when the page read gave none), or the shop owner paused or
    the run is held by the step limit."""
    job = session.jobs.first()
    assert job is not None
    if messages.page_panel_wanted(session):
        return True
    if job.page_text and not job.photos.filter(from_video=None).exists():
        return True
    if SceneStep.objects.filter(scene__job=job, status=SceneStep.Status.RUNNING).exists():
        return True
    if loop.paused(session) or loop.held_by_the_step_limit(session):
        return True
    spoke = session.messages.filter(role=Message.Role.USER).order_by("seq").last()
    asked = session.tool_calls.filter(agent=PRODUCER.name, tool="ask_owner")
    if spoke is not None:
        asked = asked.filter(created_at__gt=spoke.created_at)
    if asked.exists():
        return True
    if OwnerQuestion.objects.filter(
        scene__job=job, answered_at__isnull=True, answer__isnull=True
    ).exists():
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
    twice. One that hasn't been seen for too long has died, and is started again."""
    with transaction.atomic():
        session = Session.objects.select_for_update().get(pk=session_id)
        dead_after = timedelta(seconds=settings.PRODUCER_DEAD_AFTER_SECONDS)
        seen = session.producer_seen_at
        if session.producer_running and seen is not None and seen > timezone.now() - dead_after:
            return
        session.producer_running = True
        session.producer_seen_at = timezone.now()
        session.save(update_fields=["producer_running", "producer_seen_at"])
        transaction.on_commit(lambda: run_producer.delay(session_id))


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
    running step only counts while its heartbeat is recent."""
    others = Session.objects.exclude(pk=session.pk)
    if others.filter(producer_running=True).exists():
        return True
    alive_since = timezone.now() - timedelta(seconds=settings.STEP_DEAD_AFTER_SECONDS)
    return (
        SceneStep.objects.filter(status=SceneStep.Status.RUNNING, seen_at__gt=alive_since)
        .exclude(tool_call__session=session)
        .exists()
    )


def _picture_finished(step: SceneStep, picture: ProducedItem | None) -> str:
    assert step.photo is not None, "a starting picture is made from a photo"
    assert picture is not None, "a starting picture step makes its picture"
    number = step.scene.number
    # A finished scene is planned again here, in the transaction that finishes the step and
    # tells the producer, so a worker that stops first leaves the scene with its old clip.
    # Its clip is made by code, never by the producer (decision 2).
    unfinished = (
        f"Scene {number} is no longer finished. "
        if Scene.objects.get(pk=step.scene_id).plan_again()
        else ""
    )
    return (
        f"Background step finished: scene {number}'s starting picture is ready "
        f"(version {picture.version}), and is shown to the shop owner in the chat. "
        f"Photo {step.photo.position} was used: "
        f"{step.photo_reason}{_cost(step)} {unfinished}{_needs_a_yes(step)}{_told_it_waits(step)}"
        "Tell the shop owner."
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


def _audio_finished(step: SceneStep, audio: ProducedItem | None) -> str:
    assert audio is not None, "a line's audio step makes its audio"
    number = step.scene.number
    # A B-roll line too long for any clip was shortened, or its scene is now said to camera:
    # see make_line_audio. The shop owner isn't told: the chat says so if the scene changed.
    scene = Scene.objects.get(pk=step.scene_id)
    too_long = (
        f"Background step finished: scene {number}'s line's audio takes {audio.seconds:g} "
        f"seconds to say, too long for any clip.{_cost(step)} "
    )
    if too_long_for_a_clip(step, audio) and not scene.shows:
        return (
            f"{too_long}Scene {number} is now a talking scene. Make its starting picture "
            "again, then its audio and its clip."
        )
    if too_long_for_a_clip(step, audio) and step.line in scene.shortened_from:
        return (
            f"{too_long}Scene {number}'s line was shortened to fit its clip. Make its audio again."
        )
    if too_long_for_a_clip(step, audio) and step.line == scene.line:
        # Only when the scene changed while its audio was made, so it wasn't said to camera.
        return (
            f"{too_long}Its line couldn't be shortened to fit, and scene {number} shows the "
            "product, so it isn't said to camera. Ask the shop owner for a shorter line of "
            "their own."
        )
    return (
        f"Background step finished: scene {step.scene.number}'s line's audio is ready "
        f"(version {audio.version}, {audio.seconds:g} seconds).{_cost(step)} It isn't shown "
        "to the shop owner. Transcribe it next. Tell the shop owner."
    )


def _transcript_finished(step: SceneStep, transcript: ProducedItem | None) -> str:
    assert transcript is not None and transcript.made_from is not None, (
        "a transcript is made from audio"
    )
    return (
        f"Background step finished: scene {step.scene.number}'s audio (version "
        f"{transcript.made_from.version}) was transcribed (version {transcript.version}). It "
        f'was heard as: "{transcript.text}"{_cost(step)} Tell the shop owner.'
    )


def _clip_finished(step: SceneStep, clip: ProducedItem | None) -> str:
    assert clip is not None and clip.made_from is not None, "a clip is made from its audio"
    number = step.scene.number
    ended = agent_fix(step)
    # Only a give-up from before 2026-10-23, run again, is said to camera: one since keeps
    # its last try, and its scene is finished as below.
    if ended is not None and ended.said_to_camera:
        return (
            f"Background step finished: scene {number}'s clip failed the clip judge, and the "
            f"fixing agent gave up on it after {len(clip_tries(step)) - 1} tries, saying: "
            f'"{ended.gave_up}"{_cost(step)} Scene {number} is now a talking scene. Make its '
            "starting picture again, then its audio and its clip. The chat showed the shop "
            "owner every try, so they can tap one to use it instead, for nothing. Tell the "
            "shop owner."
        )
    # The producer's fix (fix_scene): how it ended, what it left in the ad.
    if step.fixes_a_note:
        Scene.objects.filter(pk=step.scene_id).update(status=Scene.Status.FINISHED)
        waits = ended is not None and (ended.paused_at is not None or is_parked(ended))
        return f"Background step finished: {what_the_fix_did(step, clip)}{_cost(step)}" + (
            # One line for the whole change, once its ad is shown: assemble_ad says what
            # every change since the last ad cost.
            ""
            if waits
            else " Once the new ad is shown, tell the shop owner in one line what changed and "
            "what it cost."
        )
    # A clip made for the scene as it was before a plan edit is stale (spec #247 point 9):
    # kept, with every version, but it doesn't finish the scene.
    edited = stale.is_stale(clip.step or step)
    # Finished here, in the transaction that finishes the step, never as its first clip is kept:
    # a step that stops before then leaves its scene unfinished, as a picture's does.
    if not edited:
        Scene.objects.filter(pk=step.scene_id).update(status=Scene.Status.FINISHED)
    # A B-roll clip made before every B-roll scene got a picture may have none.
    picture = f"starting picture version {clip.picture.version} and " if clip.picture else ""
    note = f', with its movement rewritten from the note "{step.note}"' if step.note else ""
    waiting = "".join(
        f" The chat told the shop owner that scene {scene.number} waits for their yes on "
        f"{part.what} in its starting picture, so the ad can't be assembled yet: don't tell "
        "them again."
        for scene, part in say_what_waits_on_the_shop_owner(step.scene.job, step)
    )
    # What was cut to the video API's limits, kept on the step as its clip was sent.
    cut = f" {step.result}" if step.result else ""
    return (
        f"Background step finished: scene {number}'s clip is ready (version {clip.version}, "
        f"{clip.seconds:g} seconds), made from {picture}audio version "
        f"{clip.made_from.version}{note}.{cut}{_cost(step)}{what_the_judge_did(step, clip)} "
        + (
            f"It was made for scene {number} as it was before the plan changed, so it is stale "
            f"and scene {number} isn't finished: make its stale steps again if the scene "
            "needs them."
            if edited
            else f"Scene {number} is finished."
        )
        + f"{waiting} Tell the shop owner."
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


STEP_WORK = {
    SceneStep.Kind.STARTING_PICTURE: StepWork(
        make=make_starting_picture,
        finished=_picture_finished,
        shown=lambda step, picture: (
            [
                messages.AttachedFile(
                    Attachment.Kind.PICTURE, picture.file, step.scene.number, version_label(picture)
                )
            ]
            if picture
            else []
        ),
    ),
    # The line's audio and its transcript are the producer's to judge, not the shop owner's.
    SceneStep.Kind.LINE_AUDIO: StepWork(
        make=make_line_audio,
        finished=_audio_finished,
        shown=lambda _step, _audio: [],
    ),
    SceneStep.Kind.TRANSCRIPT: StepWork(
        make=transcribe_line_audio,
        finished=_transcript_finished,
        shown=lambda _step, _transcript: [],
    ),
    # Each clip is shown with its scene number as it is made, so the shop owner sees each
    # scene on its own before the whole ad is put together from them.
    # One the fixing agent gave up on shows every try instead, in its own notice.
    SceneStep.Kind.CLIP: StepWork(
        make=make_clip,
        finished=_clip_finished,
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
            if clip and not agent_gave_up(step)
            else []
        ),
    ),
}


def _step_name(step: SceneStep) -> str:
    """Such as "scene 1's starting picture"."""
    return f"scene {step.scene.number}'s {STEP_NAMES[SceneStep.Kind(step.kind)]}"


@shared_task
def run_scene_step(step_id: int) -> None:
    """Do a scene step's work in the background, then have the producer told: it reads the
    result on its next turn, and is started if it isn't working."""
    step = SceneStep.objects.select_related("scene__job", "tool_call__session").get(pk=step_id)
    # Celery may hand a step out again after a crash. One that has finished or failed since
    # is left as it is, so nothing is shown or paid for twice; so is one the shop owner's
    # pause cancelled before it was taken.
    if step.status != SceneStep.Status.RUNNING:
        return
    # Taken: from now on its heartbeat says whether it is alive (see fail_dead_steps). Only
    # while it is still running, in one statement, so a pause cancelling it at the same
    # moment either cancels it first, and it is left, or finds it taken, and keeps it.
    now = timezone.now()
    if not SceneStep.objects.filter(pk=step_id, status=SceneStep.Status.RUNNING).update(
        taken_at=now, seen_at=now
    ):
        return
    step.taken_at = now
    session = step.tool_call.session
    work = STEP_WORK[SceneStep.Kind(step.kind)]
    try:
        # Its trace part is open until the step is finished, so it shows what was handed back.
        with tracing.scene_step(
            kind=step.kind,
            scene=step.scene.number,
            session_id=str(session.pk),
            trace_id=step.tool_call.trace_id,
            parent_id=step.tool_call.observation_id,
        ) as traced:
            # Before the work, so a step that fails still shows what it was asked.
            _asked(traced, step, prompt="")
            with (
                _beating(
                    settings.STEP_HEARTBEAT_SECONDS,
                    lambda: SceneStep.objects.filter(pk=step_id).update(seen_at=timezone.now()),
                    f"Scene step {step_id}",
                ),
                charged_to(step.tool_call),
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
    # producer is woken in the same transaction, so its session never looks idle
    # between the two, when another session could start.
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
        # A fix resumed with the shop owner's answer changed the scene's clip in an ad
        # already shown: the producer assembles it again, as after every other fix (its
        # step's result says so), never code.
        wake_producer(str(session.pk))


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
STEP_TOOLS = {
    SceneStep.Kind.STARTING_PICTURE: "make_starting_picture",
    SceneStep.Kind.LINE_AUDIO: "make_line_audio",
    SceneStep.Kind.TRANSCRIPT: "transcribe_line_audio",
    SceneStep.Kind.CLIP: "make_clip",
}


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
        "new one with make_clip. "
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
    return (
        f"Start it again with {STEP_TOOLS[SceneStep.Kind(step.kind)]} for scene {number}, or "
        f"skip it: {skip}. "
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
                # Before the step fails, so its scene is as the fixing agent's give-up
                # leaves it.
                end_the_agents_fixing(step)
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
    dead = SceneStep.objects.filter(
        status=SceneStep.Status.RUNNING, taken_at__isnull=False, seen_at__lt=dead_since
    ).select_related("scene", "tool_call")
    _end_stopped(
        dead, "Its work stopped partway, and it was never heard from again.", _told_it_ended
    )


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
        stopped = _end_stopped(
            SceneStep.objects.filter(status=SceneStep.Status.RUNNING).select_related(
                "scene", "tool_call"
            ),
            "The worker restarted while it was being made.",
        )
        sessions.update(step.tool_call.session_id for step in stopped)
        for session in Session.objects.filter(pk__in=sessions):
            if another_session_is_busy(session):
                continue
            # Not seen since the worker stopped, so wake_producer starts it again.
            Session.objects.filter(pk=session.pk).update(producer_seen_at=None)
            wake_producer(str(session.pk))


@worker_ready.connect
def _when_the_worker_starts(**_: object) -> None:
    carry_on_after_the_worker_starts()
    connection.close()
