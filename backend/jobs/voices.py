"""The presenter's voice, designed through the voice service, whose account holds only so many
voices (spec round-7-other-reds section 2a). In round 7 the account filled, as nothing ever
deleted a voice, and every ad that wanted a presenter stopped. Now, when it is full, the
voices of this machine's ad that has gone longest without activity are deleted to make room,
and the voice is designed again, once.

Each voice is kept under a name that says which machine designed it and for which session,
so one machine never deletes another's, and voices kept before names said so ("AdForge
presenter") are never deleted by code. The ad whose voices are deleted keeps everything made
in them; a later line change there gets a newly designed voice."""

from uuid import UUID

from django.conf import settings
from django.db.models import Exists, Max, OuterRef, Q
from django.db.models.functions import Coalesce

from agents.models import ToolCall
from chat.models import Message, Session
from gateway.gateway import delete_voice, design_voice, voice_names
from gateway.types import VoiceAccountFull

from . import notices
from .job_locks import one_at_a_time
from .models import Job, ProducedItem, SceneStep

# The Postgres advisory lock held while a voice is designed: one at a time on a machine, so
# ads designing together don't crowd the voice service's design limit (round 7: 8 at once
# drew 429s), and two can't free the same room.
VOICE_DESIGN_LOCK = 9


def voice_name(session_id: UUID | None) -> str:
    """The name a voice is kept under in the voice account: this machine's, then the
    session's."""
    return f"AdForge {settings.LANGFUSE_ENVIRONMENT} {session_id}"


def designed_voice(job: Job, *, description: str, sample: str) -> str:
    """A voice designed for `job` from `description`, heard saying `sample`, kept in the
    voice account under the job's session's name. Returns its id. When the account is full,
    the voices of this machine's longest idle ad are deleted to make room, and it is designed
    again, once; when none can be deleted, VoiceAccountFull goes up."""
    with one_at_a_time(VOICE_DESIGN_LOCK, "voice design"):
        try:
            return _design(job, description, sample)
        except VoiceAccountFull as full:
            if not _free_room(job, full):
                raise
        return _design(job, description, sample)


def _design(job: Job, description: str, sample: str) -> str:
    return design_voice(
        job=job,
        purpose="design_voice",
        description=description,
        sample=sample,
        name=voice_name(job.session_id),
    )


def _free_room(job: Job, full: VoiceAccountFull) -> bool:
    """Delete the voices of this machine's ad that has gone longest without activity and has
    no work running, other than `job`'s, so the voice account has room; each is recorded on
    both ads. Gives whether any were deleted."""
    kept = voice_names()
    for session in _idle_sessions(but=job.session_id):
        voices = ProducedItem.objects.filter(
            job__session=session, kind=ProducedItem.Kind.VOICE, voice_deleted=False
        ).exclude(voice_id="")
        ours = [voice for voice in voices if kept.get(voice.voice_id) == voice_name(session.pk)]
        if not ours:
            continue
        for voice in ours:
            # Marked first: a voice marked but not deleted only costs a new voice later; one
            # deleted but not marked would fail every line said in it.
            ProducedItem.objects.filter(pk=voice.pk).update(voice_deleted=True)
            delete_voice(voice.voice_id)
        said = (
            f"The voice service's account was full ({full}), so the {len(ours)} voice(s) of "
            f'the ad "{session}" ({session.pk}), idle the longest, were deleted to make room'
        )
        notices.post_notice(job, notices.VOICE_FREED, f"{said}.", Message.Level.INFO)
        notices.record(
            session,
            notices.VOICE_FREED,
            f"{said} for the ad {job.session_id}. A new voice is designed when this ad next "
            "needs one.",
            Message.Level.INFO,
        )
        return True
    return False


def _idle_sessions(*, but: UUID | None) -> list[Session]:
    """This machine's sessions with a voice and no work running, but `but`, the one idle
    longest first: by their last message, or when they started if they have none."""
    running_steps = SceneStep.objects.filter(
        scene__job__session=OuterRef("pk"), status=SceneStep.Status.RUNNING
    )
    running_tools = ToolCall.objects.filter(session=OuterRef("pk"), finished_at=None)
    with_voices = ProducedItem.objects.filter(
        job__session=OuterRef("pk"), kind=ProducedItem.Kind.VOICE, voice_deleted=False
    ).exclude(voice_id="")
    others = Session.objects.exclude(pk=but) if but is not None else Session.objects.all()
    return list(
        others.filter(Exists(with_voices), producer_running=False)
        .exclude(Q(Exists(running_steps)) | Q(Exists(running_tools)))
        .annotate(active_at=Coalesce(Max("messages__created_at"), "created_at"))
        .order_by("active_at")
    )
