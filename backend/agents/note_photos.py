"""Photos the shop owner sends with a scene's note on the scene cards (Julian, Oct 29): each
is kept with its scene (chat Attachment.note_scene), and the producer can't end its turn
until each is in that scene's references or it asked the shop owner about it. Code checks it
as the producer goes to end its turn, so it can't quietly leave a photo out."""

from django.db.models import QuerySet
from django.utils import timezone

from chat import messages
from chat.models import Attachment, Message, Session
from jobs import notices
from jobs.library import OwnerPhoto, library_of
from jobs.models import Job
from jobs.notices import post_notice
from jobs.scene_references import original_number

from .loop import paused
from .models import SystemNote, ToolCall

# How many times code holds the producer's turn open for the same photos before it lets the
# turn end and tells the shop owner in red that a photo wasn't used.
MOST_REMINDERS = 2


def hold_the_turn(session: Session) -> bool:
    """Whether code holds the producer's turn open as it goes to end it: a photo sent with a
    scene's note isn't in that scene's references and the producer hasn't asked the shop
    owner about it since. Holding it leaves the producer a system note naming each such
    photo. After MOST_REMINDERS notes for the same photos the turn ends, and the shop owner
    is told in red. Never while the shop owner's pause holds: putting a photo in the library
    costs money."""
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
    the plan, or the photo is in its scene's references, as the clean picture drawn from it
    or a version of that."""
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
    items = {item.number: item for item in library_of(job)}
    for picture in scene.scene_pictures:
        number = picture["number"]
        if number not in items:
            continue
        source = items[original_number(job, number)].source
        if isinstance(source, OwnerPhoto) and source.original == photo.file:
            return True
    return False


def _reminder(waiting: list[Attachment]) -> str:
    """The system note holding the producer's turn open for the photos `waiting`."""
    unused = " ".join(
        f"Owner photo {messages.owner_photo_number(photo)}, sent with the Scene "
        f"{photo.note_scene} note, isn't in {_scene_now(photo)}"
        for photo in waiting
    )
    put = "Put it there" if len(waiting) == 1 else "Put each there"
    about = "it" if len(waiting) == 1 else "them"
    return (
        f"{unused} {put} (add_to_library, then set_references), or ask the shop owner about "
        f"{about} with ask_owner, naming {about} in the question's photos. Your turn can't end "
        "before."
    )


def _scene_now(photo: Attachment) -> str:
    """Where `photo` isn't yet, its scene by its number now: "scene 2's references yet.", and
    once a plan edit moved the scene, "scene 3's references yet (scene 2 is scene 3 now)."
    """
    assert photo.note_for is not None
    now, then = photo.note_for.number, photo.note_scene
    moved = "" if now == then else f" (scene {then} is scene {now} now)"
    return f"scene {now}'s references yet{moved}."


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
