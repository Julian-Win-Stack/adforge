"""The shop owner puts a scene's version in the ad from its card (clip version picker, Julian
2026-10-29 22:16). Code swaps it in, for nothing (jobs/card_versions.py), leaves the producer
one system note saying so, and wakes it to read it; before the producer reads the note, code
assembles the ad again with the swap once an ad has been shown, and the note says how that
went. The producer takes no step for the swap: it only tells the shop owner."""

from django.db import transaction

from chat.models import Session
from jobs import card_versions
from jobs.card_versions import CantUse

from .models import SystemNote
from .producer import assembled_again


def use(session: Session, scene: int, number: int) -> None:
    """Put scene `scene`'s version `number` in the ad, as the shop owner tapped on its card,
    tell the producer and wake it. Refuses with CantUse, saying why, as card_versions.use
    does, and a scene the ad hasn't got."""
    from .tasks import wake_producer

    job = session.jobs.first()
    found = job.scenes.filter(number=scene).first() if job is not None else None
    if found is None:
        raise CantUse(f"This ad has no scene {scene}.")
    with transaction.atomic():
        gone = card_versions.use(found, number)
        SystemNote.objects.create(
            session=session,
            kind=SystemNote.Kind.VERSION_USED,
            text=(
                f"The shop owner put scene {scene}'s v{number} in the ad from the scene's card, "
                f"so it is the scene's clip now, as {gone.version.label}: nothing was made, "
                "judged or paid for. It is done: don't swap it again."
            ),
        )
        wake_producer(str(session.pk))


def assemble_after_swaps(session: Session) -> None:
    """Once an ad has been shown, assemble it again with the versions the shop owner swapped
    in from the cards since the producer last read, and add how it went to the newest note of
    them, for the producer to tell the shop owner. Nothing to do when no swap waits, or the ad
    was already assembled for them."""
    note = (
        session.system_notes.filter(kind=SystemNote.Kind.VERSION_USED, read_at__isnull=True)
        .order_by("created_at", "id")
        .last()
    )
    if note is None or note.assembled:
        return
    note.text += assembled_again(session, session.jobs.first())
    note.assembled = True
    note.save(update_fields=["text", "assembled"])
