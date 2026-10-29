"""Adding to a conversation. Every message in a session goes through here, so they are
always numbered in the order they become visible."""

from collections.abc import Sequence
from typing import NamedTuple

from django.db import transaction
from django.db.models import Max
from django.utils.text import Truncator

from jobs.models import ProducedItem

from .models import Attachment, Message, Session

# How long a session's name may be, once it is taken from the first message.
NAME_LENGTH = 80

# The result of the page panel's message once its pasted page is kept as the page.
PASTE_ACCEPTED = "accepted"


class AttachedFile(NamedTuple):
    """A file for a message to carry. It is already in the file store. `scene` is the
    number of the scene it is the starting picture or clip of, if any, and `version` the
    label of the scene version it is shown as, and `clip` the clip a video shows, so the
    chat can show what that clip was sent."""

    kind: Attachment.Kind
    file: str
    scene: int | None = None
    version: str = ""
    clip: ProducedItem | None = None


class StepSignal(NamedTuple):
    """What a message says of a scene's starting picture or clip: that it started, was
    made, or ended with nothing made."""

    scene: int
    kind: Message.StepKind
    state: Message.StepState


class SuggestedNote(NamedTuple):
    """A note for the shop owner to send or change about scene `scene`, filling its card."""

    scene: int
    text: str


class PlanSignal(NamedTuple):
    """What a message says of fix plan `plan` (its id): shown, a tap of its yes button, or
    settled."""

    plan: int
    event: Message.PlanEvent


def add(
    session: Session,
    *,
    role: Message.Role,
    text: str = "",
    level: Message.Level | None = None,
    carrying: Sequence[AttachedFile] = (),
    step: StepSignal | None = None,
    suggested: SuggestedNote | None = None,
    plan: PlanSignal | None = None,
    tries_of: int | None = None,
    chosen_clip: int | None = None,
    owner_question: int | None = None,
    pasted_page: bool = False,
    pasted_page_result: str = "",
    wants_pasted_page: bool | None = None,
    wants_more_photos: bool = False,
) -> Message:
    """Say something in a session, with any files it carries, and name the session from
    the first thing the user types. A notice carries its `level`; nothing else does. A
    message about a scene's starting picture or clip carries its `step`, and one may
    suggest a note on a scene, and one about a fix plan carries its `plan`. One showing every
    try of a clip the judge failed names its clip step, `tries_of`, and the shop owner's tap
    on one of them names the try chosen, `chosen_clip`. The notice saying the fixing agent
    asked the shop owner a question names it, `owner_question`. A notice about the product page says
    whether it wants the page panel, `wants_pasted_page`. The page panel's message is a
    `pasted_page`; it and code's notice answering it carry the `pasted_page_result`, and a
    notice keeping one with too few photos `wants_more_photos`.

    Messages are numbered 1, 2, 3... per session. The session row is locked while
    numbering, so messages always become visible in number order. Without that, a poll
    could see #12 before #11 is saved, move its "since" marker past 11, and never show #11.
    For the same reason a message and its files are saved together: a poll that saw the
    message first would move past it and never show the picture.
    """
    with transaction.atomic():
        locked = Session.objects.select_for_update().get(pk=session.pk)
        last = locked.messages.aggregate(last=Max("seq"))["last"] or 0
        message = Message.objects.create(
            session=locked,
            seq=last + 1,
            role=role,
            level=level or "",
            text=text,
            step_scene=step.scene if step else None,
            step_kind=step.kind if step else "",
            step_state=step.state if step else "",
            suggested_note=suggested.text if suggested else "",
            suggested_note_scene=suggested.scene if suggested else None,
            plan_event=plan.event if plan else "",
            fix_plan_id=plan.plan if plan else None,
            tries_of_id=tries_of,
            chosen_clip_id=chosen_clip,
            owner_question_id=owner_question,
            pasted_page=pasted_page,
            pasted_page_result=pasted_page_result,
            wants_pasted_page=wants_pasted_page,
            wants_more_photos=wants_more_photos,
        )
        Attachment.objects.bulk_create(
            Attachment(
                message=message,
                position=position,
                kind=attached.kind,
                file=attached.file,
                scene=attached.scene,
                version=attached.version,
                clip=attached.clip,
            )
            for position, attached in enumerate(carrying, start=1)
        )
        # A user who started with only photos gets the name from the first words they type.
        if role == Message.Role.USER and text and not locked.name:
            locked.name = name_from(text)
            locked.save(update_fields=["name"])
    return message


def as_read(message: Message, *, numbered: bool = False) -> str:
    """The message as an agent reads it: its words, then a note of the files it carries,
    since a model given the conversation can't see them. A message may carry only files.
    `numbered` numbers the shop owner's photos through the chat, as "owner photo 3", so the
    producer's fix can name which photo shows which thing; no other model is told, so what
    the judges read is as it was."""
    counts: dict[str, int] = {}
    for attachment in message.attachments.all():
        counts[attachment.kind] = counts.get(attachment.kind, 0) + 1
    if not counts:
        return message.text
    carried = [f"{count} {kind}{'s' if count != 1 else ''}" for kind, count in counts.items()]
    pictures = counts.get(Attachment.Kind.PICTURE, 0)
    numbers_said = ""
    if numbered and message.role == Message.Role.USER and pictures:
        first = _owner_photos_before(message) + 1
        numbers = [str(number) for number in range(first, first + pictures)]
        listed = numbers[0] if pictures == 1 else f"{', '.join(numbers[:-1])} and {numbers[-1]}"
        numbers_said = f": owner photo{'s' if pictures != 1 else ''} {listed}"
    note = f"[Attached {' and '.join(carried)}{numbers_said}]"
    return f"{message.text}\n\n{note}" if message.text else note


def owner_photos(session: Session) -> list[Attachment]:
    """Every photo the shop owner attached in the chat, in the order sent: owner photo 1
    first."""
    return list(
        Attachment.objects.filter(
            message__session=session,
            message__role=Message.Role.USER,
            kind=Attachment.Kind.PICTURE,
        ).order_by("message__seq", "position")
    )


def page_panel_wanted(session: Session) -> bool:
    """Whether the chat should show the page panel: the latest notice about it asked for it,
    as when the product page couldn't be read, no page has been read since, and no page the
    shop owner pasted after it has been kept."""
    latest = (
        session.messages.filter(wants_pasted_page__isnull=False)
        .order_by("-seq")
        .values_list("seq", "wants_pasted_page")
        .first()
    )
    if latest is None or not latest[1]:
        return False
    return not session.messages.filter(
        seq__gt=latest[0], pasted_page_result=PASTE_ACCEPTED
    ).exists()


def _owner_photos_before(message: Message) -> int:
    """How many photos the shop owner attached in the chat before `message`."""
    return Attachment.objects.filter(
        message__session_id=message.session_id,
        message__role=Message.Role.USER,
        message__seq__lt=message.seq,
        kind=Attachment.Kind.PICTURE,
    ).count()


def name_from(text: str) -> str:
    """A session's name, taken from the first thing the user said."""
    return Truncator(" ".join(text.split())).chars(NAME_LENGTH)
