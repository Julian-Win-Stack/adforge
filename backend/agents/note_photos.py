"""Photos and videos sent with scene notes must reach their scenes or be explained.

Photos reach a scene's agent through the library and a note handoff. Videos reach the
scene as a watched moment. A named question or an explicit reason why a file cannot be
used also settles it, so no attachment is quietly dropped at the end of a producer turn.
"""

from django.db.models import QuerySet
from django.utils import timezone

from chat import messages
from chat.models import Attachment, Message, Session
from jobs import notices
from jobs.library import LibraryEntry, OwnerPhoto, library_of, watch_of
from jobs.models import Job, SceneAgentRun
from jobs.notices import post_notice

from .loop import paused
from .models import SystemNote, ToolCall

# How many times code holds the producer's turn open for the same attachments before it
# lets the turn end and tells the shop owner they weren't used.
MOST_REMINDERS = 2


def hold_the_turn(session: Session) -> bool:
    """Hold the turn while a scene-note attachment still needs to reach its scene.

    After MOST_REMINDERS, end the turn and tell the owner which files weren't used.
    Pausing bypasses the check because adding files to the library costs money.
    """
    if paused(session):
        return False
    waiting = _waiting(session)
    if not waiting:
        return False
    reminded = SystemNote.objects.filter(
        session=session,
        kind=SystemNote.Kind.NOTE_PHOTO,
        # Counted since the newest file came, so each file gets every reminder.
        created_at__gte=waiting[-1].message.created_at,
    ).count()
    if reminded >= MOST_REMINDERS:
        _say_unused(session, waiting)
        return False
    SystemNote.objects.create(
        session=session, kind=SystemNote.Kind.NOTE_PHOTO, text=_reminder(waiting)
    )
    return True


def _waiting(session: Session) -> list[Attachment]:
    """Note attachments still owed something; settle those that no longer are."""
    attachments: QuerySet[Attachment] = (
        Attachment.objects.filter(
            message__session=session,
            message__role=Message.Role.USER,
            kind__in=[Attachment.Kind.PICTURE, Attachment.Kind.VIDEO],
            note_scene__isnull=False,
            settled_at__isnull=True,
        )
        .select_related("message", "note_for")
        .order_by("message__seq", "position")
    )
    job = session.jobs.first()
    waiting = []
    for attachment in attachments:
        if job is not None and not _settled(job, attachment):
            waiting.append(attachment)
            continue
        attachment.settled_at = timezone.now()
        attachment.save(update_fields=["settled_at"])
    return waiting


def _settled(job: Job, attachment: Attachment) -> bool:
    """A named question, a removed scene or a completed scene handoff settles a file.

    A video handoff is its scene's selected watched moment. A rejected video already has
    an owner notice, so the producer owes no further explanation for that file.
    """
    is_photo = attachment.kind == Attachment.Kind.PICTURE
    number = (
        messages.owner_photo_number(attachment)
        if is_photo
        else messages.owner_video_number(attachment)
    )
    named = {f"arguments__{'photos' if is_photo else 'videos'}__contains": [number]}
    asked = ToolCall.objects.filter(
        session=job.session,
        agent="producer",
        tool="ask_owner",
        answer="made",
        created_at__gt=attachment.message.created_at,
        **named,
    ).exists()
    if asked:
        return True
    scene = attachment.note_for
    if scene is None or scene.removed_at is not None:
        return True
    if is_photo:
        return _first_handoff(job, attachment, finished=True) is not None
    video = job.videos.filter(file=attachment.file).first()
    if video is None:
        return False
    if video.thrown_out:
        return True
    watch = watch_of(video)
    piece = scene.piece
    return (
        watch is not None
        and piece is not None
        and piece.get("video") == video.position
        and any(moment.number == piece.get("moment") for moment in watch.moments)
    )


def _first_handoff(
    job: Job, photo: Attachment, *, finished: bool
) -> tuple[SceneAgentRun, LibraryEntry] | None:
    """The first real scene handoff made after this photo entered its library.

    Run time matters: every checkpoint in a producer turn is written before any tool
    runs, so an add_to_library followed by pass_note has a newer asset than call time.
    """
    from .scene_agent import PENDING_NOTE_DELIVERED

    if photo.note_for_id is None:
        return None
    originals = [
        item
        for item in library_of(job)
        if isinstance(item.source, OwnerPhoto) and item.source.original == photo.file
    ]
    if not originals:
        return None
    added_at = dict(
        job.library.filter(number__in=[item.number for item in originals]).values_list(
            "number", "created_at"
        )
    )
    windows = (
        SceneAgentRun.objects.filter(
            agent_fix__is_scene_agent=True,
            agent_fix__step__scene_id=photo.note_for_id,
            created_at__gte=photo.message.created_at,
        )
        .exclude(reason=PENDING_NOTE_DELIVERED)
        .select_related("agent_fix__step__tool_call")
        .order_by("created_at", "pk")
    )
    for window in windows:
        checkpoints = {
            quote["handed_by"] for quote in window.quotes if isinstance(quote.get("handed_by"), int)
        }
        if window.number == 1:
            checkpoints.add(window.agent_fix.step.tool_call_id)
        calls = ToolCall.objects.filter(
            pk__in=checkpoints,
            job=job,
            session=job.session,
            agent="producer",
            tool__in=["start_scene_agent", "pass_note"],
            created_at__gte=photo.message.created_at,
        ).exclude(answer="cant")
        if finished:
            calls = calls.filter(answer="made", finished_at__isnull=False)
        if not calls.exists():
            continue
        for item in originals:
            if added_at[item.number] <= window.created_at:
                return window, item
    return None


def photos_handed_with(window: SceneAgentRun) -> list[tuple[int, LibraryEntry]]:
    """Code's attachment metadata for a run, separate from checked owner words.

    A worker can read its real run before the producer's checkpoint answer is committed.
    End-of-turn settlement separately requires a successful finished checkpoint.
    """
    scene = window.agent_fix.step.scene
    photos = (
        Attachment.objects.filter(
            note_for=scene,
            kind=Attachment.Kind.PICTURE,
            message__role=Message.Role.USER,
            message__created_at__lte=window.created_at,
        )
        .select_related("message")
        .order_by("message__seq", "position")
    )
    handed = []
    for photo in photos:
        receipt = _first_handoff(scene.job, photo, finished=False)
        if receipt is not None and receipt[0].pk == window.pk:
            handed.append((messages.owner_photo_number(photo), receipt[1]))
    return handed


def _reminder(waiting: list[Attachment]) -> str:
    """The system note holding the producer's turn open for the attachments `waiting`."""
    unused = " ".join(
        f"{_owner_file_name(file)}, sent with the Scene "
        f"{file.note_scene} note, isn't in {_scene_now(file)}"
        for file in waiting
    )
    about = "it" if len(waiting) == 1 else "them"
    handoff = (
        "then hand each scene its note with pass_note."
        if all(file.kind == Attachment.Kind.PICTURE for file in waiting)
        else "then attach a moment from each video to its scene with set_references; "
        "hand any photo's scene its note with pass_note."
    )
    return (
        f"{unused} Put {about} in the library with add_to_library, {handoff} "
        "If a file can't be used, call cant_use_owner_files naming it "
        "with a plain reason for the shop owner. Ask with ask_owner, naming the file "
        "in photos or videos, "
        "only when the note itself is vague. Your turn can't end before."
    )


def _owner_file_name(file: Attachment) -> str:
    if file.kind == Attachment.Kind.PICTURE:
        return f"Owner photo {messages.owner_photo_number(file)}"
    return f"Owner video {messages.owner_video_number(file)}"


def _scene_now(file: Attachment) -> str:
    """The note's target by its number now, retaining its number as sent after a reorder."""
    assert file.note_for is not None
    now, then = file.note_for.number, file.note_scene
    moved = "" if now == then else f" (scene {then} is scene {now} now)"
    where = "note to its agent" if file.kind == Attachment.Kind.PICTURE else "video moment"
    return f"scene {now}'s {where} yet{moved}."


def _say_unused(session: Session, waiting: list[Attachment]) -> None:
    """Tell the owner which note files were left out after the reminders, and settle them."""
    job = session.jobs.first()
    assert job is not None
    scenes: dict[tuple[int, str], int] = {}
    for file in waiting:
        assert file.note_scene is not None
        kind = "photo" if file.kind == Attachment.Kind.PICTURE else "video"
        key = (file.note_scene, kind)
        scenes[key] = scenes.get(key, 0) + 1
    for (scene, kind), count in scenes.items():
        named = f"The {kind} you sent" if count == 1 else f"The {count} {kind}s you sent"
        were = "wasn't" if count == 1 else "weren't"
        it = "it" if count == 1 else "them"
        post_notice(
            job,
            notices.REPLY,
            f"{named} with your Scene {scene} note {were} used in scene {scene}. Send {it} "
            f"again with your note, or tell me what to do with {it}.",
            Message.Level.INFO,
            scene=scene,
        )
    Attachment.objects.filter(pk__in=[file.pk for file in waiting]).update(
        settled_at=timezone.now()
    )
