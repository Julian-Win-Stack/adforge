"""The page panel: when the product page can't be read from its link, the shop owner pastes
the page's text and attaches its photos, and sends them in one message. Code, not a model,
keeps them as the page, before the producer works: the page check judges the pasted text as
it would a page read from the link; a page that passes is kept with the photos as the
product photos, the chat says so in one line, and the producer is told in a system note, so
it plans or asks as after a normal read. A page that fails keeps nothing, and the chat says
why, so the shop owner can paste it again.

The owner is the source of what they pasted (ADR 0002's user-source rule), so it is the page
every later check reads. The pasted message itself is never given to a model as something
said in the chat: it is the page now."""

from dataclasses import dataclass

from django.db import transaction
from django.db.models import Max, QuerySet

from adforge import file_store
from adforge.retry import OutsideServiceDown
from chat import messages
from chat.models import Attachment, Message, Session
from gateway.types import UnusableReply
from jobs import page, photos
from jobs.models import Job, ProductPhoto
from jobs.notices import post_notice
from jobs.work import check_page, copy_page_text, keep_page, note_face

from .models import SystemNote
from .producer import FEWEST_SURE_PHOTOS

# Why a pasted page whose check or copy couldn't be finished isn't used.
CHECK_FAILED = "Checking it failed ({error}), so nothing was kept. Send it again to try again."
# Why one sent after the page was already kept isn't.
ALREADY_KEPT = "The product's page is already read."


def take_pastes(session: Session) -> None:
    """Keep each page the shop owner pasted in the page panel that code hasn't looked at
    yet, oldest first, or say why it can't be used."""
    for message in unlooked_at(session).order_by("seq"):
        _take(message)


def unlooked_at(session: Session) -> QuerySet[Message]:
    """The pages the shop owner pasted in the page panel that code hasn't looked at yet."""
    return session.messages.filter(role=Message.Role.USER, pasted_page=True, pasted_page_result="")


@dataclass(frozen=True)
class _Kept:
    """A pasted page that passed its check, and what is kept of it."""

    download: page.Download
    product_page: page.ProductPage
    text: str
    # Each new photo's attachment, with its Face note: a copy of one before it is left out.
    photos: list[tuple[Attachment, bool]]


def _take(message: Message) -> None:
    job = Job.objects.get(session_id=message.session_id)
    if job.page_html_key:
        _refuse(job, message, ALREADY_KEPT)
        return
    attached = list(message.attachments.filter(kind=Attachment.Kind.PICTURE))
    # The pasted text stands in for the page at the shop's link, with its photos.
    download = page.Download(
        final_url=job.product_url,
        content=message.text.encode(),
        content_type="text/plain",
        charset="utf-8",
    )
    product_page = page.ProductPage(
        text=message.text,
        words=message.text,
        declared="",
        photo_urls=[each.file for each in attached],
        name="",
        description="",
    )
    try:
        check = check_page(job, download, product_page)
        if check.decision == "unreadable":
            _refuse(job, message, check.reason)
            return
        text = copy_page_text(job, download, product_page, record=None)
    except (UnusableReply, OutsideServiceDown) as error:
        # Left unanswered, the paste would be taken again on every message the shop owner
        # sends: it is answered, and they send it again.
        _refuse(job, message, CHECK_FAILED.format(error=error))
        return
    copies = photos.Copies()
    new = [each for each in attached if copies.is_new(each.file, file_store.read(each.file))]
    # The shop owner's photo was never seen by the picker, so it is noted on its own.
    noted = [(each, note_face(job, each.file, each.file)) for each in new]
    _keep(job, message, _Kept(download, product_page, text, noted))


def _refuse(job: Job, message: Message, why: str) -> None:
    """Keep nothing of the pasted page, and say why in the chat, on the panel's message and
    on the notice answering it."""
    with transaction.atomic():
        post_notice(
            job,
            f"Your pasted page can't be used. {why}",
            Message.Level.PROBLEM,
            pasted_page_result=why,
        )
        Message.objects.filter(pk=message.pk).update(pasted_page_result=why)


def _keep(job: Job, message: Message, kept: _Kept) -> None:
    """Keep the pasted page as the job's page and its photos as product photos, say so in the
    chat, and tell the producer: all together, so a worker that stops part-way keeps none of
    it, and the page is taken again."""
    with transaction.atomic():
        position = job.photos.aggregate(last=Max("position"))["last"] or 0
        for offset, (attached, has_face) in enumerate(kept.photos, 1):
            ProductPhoto.objects.create(
                job=job, position=position + offset, file=attached.file, has_face=has_face
            )
        keep_page(job, kept.download, kept.product_page, kept.text)
        count = len(kept.photos)
        post_notice(
            job,
            "Using your text as the page and "
            + (
                "this photo as the product photo."
                if count == 1
                else f"these {count} photos as the product photos."
            ),
            Message.Level.INFO,
            pasted_page_result=messages.PASTE_ACCEPTED,
        )
        Message.objects.filter(pk=message.pk).update(pasted_page_result=messages.PASTE_ACCEPTED)
        SystemNote.objects.create(
            session_id=message.session_id,
            kind=SystemNote.Kind.PAGE_PASTED,
            text=_told(message, kept),
        )


def _told(message: Message, kept: _Kept) -> str:
    """What the producer is told of the pasted page kept: what it was given, which owner
    photos are the product photos, and, with too few of them, to ask before planning."""
    count = len(kept.photos)
    files = {attached.file for attached, _ in kept.photos}
    numbers = [
        str(number)
        for number, attached in enumerate(messages.owner_photos(message.session), 1)
        if attached.message_id == message.pk and attached.file in files
    ]
    listed = numbers[0] if count == 1 else f"{', '.join(numbers[:-1])} and {numbers[-1]}"
    told = (
        f"The shop owner sent the product page's text ({len(message.text.split())} words) and "
        f"{count} photo{'s' if count != 1 else ''} through the page panel, as the page couldn't "
        "be read from its link. The page is read: their text is the page text, and owner "
        f"photo{'s' if count != 1 else ''} {listed} {'are' if count != 1 else 'is'} the "
        f"product photo{'s' if count != 1 else ''}."
    )
    if count < FEWEST_SURE_PHOTOS:
        told += (
            f" Stopped before planning: only {count} product photo was sent. Tell them why in "
            "plain words, and ask them to go ahead with this photo or attach more. Wait for "
            "their answer: if they attach photos, add them with use_photos, then plan."
        )
    return told
