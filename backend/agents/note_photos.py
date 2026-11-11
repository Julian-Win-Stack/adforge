"""Photos the shop owner sends with a scene's note on the scene cards (Julian, Oct 29): each
is kept with its scene (chat Attachment.note_scene), and the producer can't end its turn
until each reaches its scene's agent, is declined with a reason for the owner,
or it asked the shop owner about it. Code checks it as the producer goes to end its
turn, so it can't quietly leave a photo out."""

from django.db.models import QuerySet
from django.utils import timezone

from chat import messages
from chat.models import Attachment, Message, Session
from jobs import notices
from jobs.library import LibraryEntry, OwnerPhoto, library_of
from jobs.models import Job, SceneAgentRun
from jobs.notices import post_notice

from .loop import paused
from .models import SystemNote, ToolCall

# How many times code holds the producer's turn open for the same photos before it lets the
# turn end and tells the shop owner in red that a photo wasn't used.
MOST_REMINDERS = 2


def hold_the_turn(session: Session) -> bool:
    """Hold the turn when a scene-note photo still needs a handoff or a question.

    Every photo must reach its scene's agent after entering the library.
    A question naming the photo also releases the turn. Code posts a system reminder
    for each photo still waiting. After MOST_REMINDERS, it ends the turn and tells
    the owner. Pausing bypasses this check because adding a photo costs money.
    """
    if paused(session):
        return False
    waiting = _waiting(session)
    if not waiting:
        return False
    reminded = SystemNote.objects.filter(
        session=session,
        kind=SystemNote.Kind.NOTE_PHOTO,
        # Counted since the newest photo came, so each photo gets every reminder.
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
    """The photos sent with a scene's note that the producer still owes something: each one
    it now owes nothing is marked settled on the way."""
    photos: QuerySet[Attachment] = (
        Attachment.objects.filter(
            message__session=session,
            message__role=Message.Role.USER,
            kind=Attachment.Kind.PICTURE,
            note_scene__isnull=False,
            settled_at__isnull=True,
        )
        .select_related("message", "note_for")
        .order_by("message__seq", "position")
    )
    job = session.jobs.first()
    waiting = []
    for photo in photos:
        if job is not None and not _settled(job, photo):
            waiting.append(photo)
            continue
        photo.settled_at = timezone.now()
        photo.save(update_fields=["settled_at"])
    return waiting


def _settled(job: Job, photo: Attachment) -> bool:
    """Whether the producer owes nothing for `photo`: it asked the shop owner a question
    naming it in the question's photos since it was sent, the photo's scene is gone from
    the plan, or the photo reached its scene agent through a successful handoff."""
    photo_number = messages.owner_photo_number(photo)
    asked = ToolCall.objects.filter(
        session=job.session,
        agent="producer",
        tool="ask_owner",
        answer="made",
        created_at__gt=photo.message.created_at,
        arguments__photos__contains=[photo_number],
    ).exists()
    if asked:
        return True
    scene = photo.note_for
    if scene is None or scene.removed_at is not None:
        return True
    return _first_handoff(job, photo, finished=True) is not None


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
    """The system note holding the producer's turn open for the photos `waiting`."""
    unused = " ".join(
        f"Owner photo {messages.owner_photo_number(photo)}, sent with the Scene "
        f"{photo.note_scene} note, isn't in {_scene_now(photo)}"
        for photo in waiting
    )
    about = "it" if len(waiting) == 1 else "them"
    return (
        f"{unused} Put {about} in the library with add_to_library, then hand each scene its "
        "note with pass_note. If a file can't be used, call cant_use_owner_files naming it "
        "with a plain reason for the shop owner. Ask with ask_owner, naming the photo, "
        "only when the note itself is vague. Your turn can't end before."
    )


def _scene_now(photo: Attachment) -> str:
    """The note's target by its number now, retaining its number as sent after a reorder."""
    assert photo.note_for is not None
    now, then = photo.note_for.number, photo.note_scene
    moved = "" if now == then else f" (scene {then} is scene {now} now)"
    return f"scene {now}'s note to its agent yet{moved}."


def _say_unused(session: Session, waiting: list[Attachment]) -> None:
    """Tell the shop owner of each scene whose note's photos weren't used, and settle them:
    the producer was reminded and still left them out."""
    job = session.jobs.first()
    assert job is not None
    scenes: dict[int, int] = {}
    for photo in waiting:
        assert photo.note_scene is not None
        scenes[photo.note_scene] = scenes.get(photo.note_scene, 0) + 1
    for scene, count in scenes.items():
        photos = "The photo you sent" if count == 1 else f"The {count} photos you sent"
        were = "wasn't" if count == 1 else "weren't"
        it = "it" if count == 1 else "them"
        post_notice(
            job,
            notices.REPLY,
            f"{photos} with your Scene {scene} note {were} used in scene {scene}. Send {it} "
            f"again with your note, or tell me what to do with {it}.",
            Message.Level.INFO,
            scene=scene,
        )
    Attachment.objects.filter(pk__in=[photo.pk for photo in waiting]).update(
        settled_at=timezone.now()
    )
