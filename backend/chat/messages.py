"""Adding to a conversation. Every message in a session goes through here, so they are
always numbered in the order they become visible."""

from collections.abc import Sequence
from typing import NamedTuple

from django.db import transaction
from django.db.models import Max
from django.utils.text import Truncator

from .models import Attachment, Message, Session

# How long a session's name may be, once it is taken from the first message.
NAME_LENGTH = 80


class AttachedFile(NamedTuple):
    """A file for a message to carry. It is already in the file store."""

    kind: Attachment.Kind
    file: str


def add(
    session: Session,
    *,
    role: Message.Role,
    text: str = "",
    carrying: Sequence[AttachedFile] = (),
) -> Message:
    """Say something in a session, with any files it carries, and name the session from
    the first thing the user types.

    Messages are numbered 1, 2, 3... per session. The session row is locked while
    numbering, so messages always become visible in number order. Without that, a poll
    could see #12 before #11 is saved, move its "since" marker past 11, and never show #11.
    For the same reason a message and its files are saved together: a poll that saw the
    message first would move past it and never show the picture.
    """
    with transaction.atomic():
        locked = Session.objects.select_for_update().get(pk=session.pk)
        last = locked.messages.aggregate(last=Max("seq"))["last"] or 0
        message = Message.objects.create(session=locked, seq=last + 1, role=role, text=text)
        Attachment.objects.bulk_create(
            Attachment(message=message, position=position, kind=attached.kind, file=attached.file)
            for position, attached in enumerate(carrying, start=1)
        )
        # A user who started with only photos gets the name from the first words they type.
        if role == Message.Role.USER and text and not locked.name:
            locked.name = name_from(text)
            locked.save(update_fields=["name"])
    return message


def as_read(message: Message) -> str:
    """The message as an agent reads it: its words, then a note of the files it carries,
    since a model given the conversation can't see them. A message may carry only files."""
    counts: dict[str, int] = {}
    for attachment in message.attachments.all():
        counts[attachment.kind] = counts.get(attachment.kind, 0) + 1
    if not counts:
        return message.text
    carried = [f"{count} {kind}{'s' if count != 1 else ''}" for kind, count in counts.items()]
    note = f"[Attached {' and '.join(carried)}]"
    return f"{message.text}\n\n{note}" if message.text else note


def name_from(text: str) -> str:
    """A session's name, taken from the first thing the user said."""
    return Truncator(" ".join(text.split())).chars(NAME_LENGTH)
