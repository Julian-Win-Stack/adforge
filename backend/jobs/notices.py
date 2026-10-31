"""Notices: what code records when a step falls back to a worse source or fails, so nothing
fails quietly, and what code tells the shop owner in the chat. The producer may leave a
failure out of its reply or soften it; a notice is written by code and says exactly what
happened.

Every notice is of a kind, and its kind decides who it is for (spec #272): the shop owner
sees a notice only when they must do something or it answers something they just did; every
other kind is for the developer only. Every notice is kept in `job.warnings`, and every
failure is sent to Sentry, grouped by its kind (adforge/monitoring.py)."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from adforge import monitoring
from chat import messages
from chat.models import Message, Session

from .models import Job


@dataclass(frozen=True)
class Kind:
    """A kind of notice: its short `name`, which Sentry groups it by, whether the shop owner
    is shown it (`for_owner`), and whether it is a `failure`, sent to Sentry. A notice the
    owner isn't shown is only recorded."""

    name: str
    for_owner: bool
    failure: bool = True
    # Whether it asks the shop owner for something the ad waits on (a page, a photo, an
    # answer, a yes): the producer isn't nudged on while one stands unanswered.
    asks: bool = False


def _owner(name: str, *, failure: bool = True, asks: bool = False) -> Kind:
    return Kind(name, for_owner=True, failure=failure, asks=asks)


def _developer(name: str) -> Kind:
    return Kind(name, for_owner=False)


# What the shop owner is shown: what they must act on, and replies to what they just did.
PAGE_UNREADABLE = _owner("page_unreadable", asks=True)
NO_PRODUCT_PHOTO = _owner("no_product_photo", asks=True)
PASTE_REFUSED = _owner("paste_refused", asks=True)
PASTE_KEPT = _owner("paste_kept", failure=False)
# A pasted page kept with too few photos: the panel stays open for more.
PASTE_WANTS_PHOTOS = _owner("paste_wants_photos", failure=False, asks=True)
PANEL_CLOSED = _owner("panel_closed", failure=False)
PART_NEEDS_YES = _owner("part_needs_yes", failure=False, asks=True)
PART_NOT_DRAWN = _owner("part_not_drawn", asks=True)
# The fixing agent's question: the ad never waits for its answer (round 7 spec section 4).
AGENT_ASKED = _owner("agent_asked", failure=False)
TRIES_TO_PICK = _owner("tries_to_pick")
CHECK_IT_YOURSELF = _owner("check_it_yourself")
OWNER_FILE_UNUSABLE = _owner("owner_file_unusable", asks=True)
REPLY = _owner("reply", failure=False)
# Its why is the developer's: PRODUCER_GAVE_UP.
PRODUCER_STOPPED = _owner("producer_stopped", failure=False)

# What only the developer is shown: fallbacks, retries, refusals, setup and internal checks.
RECOLOUR_FAILED = _developer("recolour_failed")
NO_COLOUR_REFERENCE = _developer("no_colour_reference")
CUT_OUT_FAILED = _developer("cut_out_failed")
CUT_OUT_TRIED_AGAIN = _developer("cut_out_tried_again")
# A clean picture of an owner photo kept with its check giving no answer.
OWNER_PHOTO_UNCHECKED = _developer("owner_photo_unchecked")
FACE_SWAP_FAILED = _developer("face_swap_failed")
NO_PRESENTER_TO_SWAP = _developer("no_presenter_to_swap")
PLACE_NOT_DRAWN = _developer("place_not_drawn")
STEP_NOT_MADE = _developer("step_not_made")
STEP_WAITS_ON_A_QUESTION = _developer("step_waits_on_a_question")
STEP_NOT_WRAPPED_UP = _developer("step_not_wrapped_up")
FIX_NOT_CARRIED_ON = _developer("fix_not_carried_on")
LEFT_OUT_BY_THE_FILTER = _developer("left_out_by_the_filter")
REFUSED_BY_THE_FILTER = _developer("refused_by_the_filter")
PAGE_PHOTOS_NOT_KEPT = _developer("page_photos_not_kept")
PHOTO_PICKER = _developer("photo_picker")
PAGE_TEXT_NOT_COPIED = _developer("page_text_not_copied")
PASTE_NOT_CHECKED = _developer("paste_not_checked")
PAGE_VIDEO = _developer("page_video")
TOO_MANY_PICTURES = _developer("too_many_pictures")
PIECE_NOT_SENT = _developer("piece_not_sent")
NO_FRONT_PHOTO = _developer("no_front_photo")
# Prototype (lean inputs): the planner named no look picture, so the front photo went.
NO_LOOK_PICKED = _developer("no_look_picked")
# Prototype (lean inputs, item 5): a scene picture left out because a page video goes.
PICK_LEFT_FOR_VIDEO = _developer("pick_left_for_video")
NO_FIRECRAWL = _developer("no_firecrawl")
FIRECRAWL_FAILED = _developer("firecrawl_failed")
# A page read again, fresh, because its first read couldn't be used, as a shop's bot check.
PAGE_READ_AGAIN = _developer("page_read_again")
PICTURES_ON_AZURE = _developer("pictures_on_azure")
JUDGE_FIRST_QUESTION = _developer("judge_first_question")
PART_CHECK_UNCHECKED = _developer("part_check_unchecked")
PART_TRIED_AGAIN = _developer("part_tried_again")
PHOTO_NOT_LABELLED = _developer("photo_not_labelled")
LABELLED_FOR_THE_PICKER = _developer("labelled_for_the_picker")
PLANNER_NOT_SHOWN = _developer("planner_not_shown")
NO_PHOTO_FOR_A_PICTURE = _developer("no_photo_for_a_picture")
CARD_PICTURE = _developer("card_picture")
LINE_TO_CAMERA = _developer("line_to_camera")
TOOL_WITHOUT_A_WHY = _developer("tool_without_a_why")
ANSWER_BROKE_A_RULE = _developer("answer_broke_a_rule")
FACE_NOT_NOTED = _developer("face_not_noted")
PRODUCER_NUDGED = _developer("producer_nudged")
PRODUCER_RETRIED = _developer("producer_retried")
PRODUCER_GAVE_UP = _developer("producer_gave_up")
# The voice account was full, so an idle ad's voices were deleted (spec round-7-other-reds 2a).
VOICE_FREED = _developer("voice_freed")
# Prototype (lean inputs): the fixing agent wrote more than one action sentence; the first went.
AGENT_SENTENCE_CUT = _developer("agent_sentence_cut")
# Prototype (lean inputs): the fixing agent's clean_picture move couldn't clean the picture.
CLEAN_AGAIN_FAILED = _developer("clean_again_failed")

KINDS = tuple(kind for kind in globals().values() if isinstance(kind, Kind))


def post_notice(
    job: Job,
    kind: Kind,
    text: str,
    level: Message.Level,
    carrying: Sequence[messages.AttachedFile] = (),
    step: messages.StepSignal | None = None,
    suggested: messages.SuggestedNote | None = None,
    tries_of: int | None = None,
    owner_question: int | None = None,
    pasted_page_result: str = "",
    wants_pasted_page: bool | None = None,
    wants_more_photos: bool = False,
    scene: int | None = None,
) -> Message | None:
    """Record that something fell back or failed, or tell the shop owner what they can act
    on, as the notice's `kind` says. `text` says what happened: for the developer, what
    failed, what was used instead, and what that means for the ad; for the owner, in plain
    words, what we need and what happens next. Every notice is kept in `job.warnings`, so
    the ad can be found later, and a failure is sent to Sentry, tagged with its `scene` (or
    its `step`'s). No model is ever given a notice: the producer learns of a failure from
    the tool's result.

    Only a notice for the owner is posted in the chat, with what it carries: a notice about a
    scene's starting picture or clip carries its `step`, and one may suggest a note on a
    scene; one showing every try of a clip the judge failed names its clip step, `tries_of`;
    one saying the fixing agent asked the shop owner a question names it, `owner_question`;
    one about the product page says whether it wants the page panel, `wants_pasted_page`;
    one answering the page panel's message carries its `pasted_page_result`, and one keeping
    a pasted page with too few photos `wants_more_photos`. Gives the message posted, or None
    for a notice only recorded or a job with no chat."""
    shows = carrying or suggested or tries_of or owner_question or pasted_page_result
    assert kind.for_owner or not (shows or wants_pasted_page is not None or wants_more_photos), (
        "only a notice the shop owner is shown carries anything for the chat"
    )
    warning = {"level": level, "text": text, "at": timezone.now().isoformat(), "kind": kind.name}
    # The job is read again under a lock, so a notice posted through another copy of the job
    # in the meantime isn't overwritten; the warning and the message are kept together.
    with transaction.atomic():
        locked = Job.objects.select_for_update().get(pk=job.pk)
        locked.warnings.append(warning)
        locked.save(update_fields=["warnings"])
        # A job started before sessions existed has no chat to post in.
        posted = None
        if kind.for_owner and locked.session is not None:
            posted = messages.add(
                locked.session,
                role=Message.Role.NOTICE,
                text=text,
                level=level,
                carrying=carrying,
                step=step,
                suggested=suggested,
                tries_of=tries_of,
                owner_question=owner_question,
                pasted_page_result=pasted_page_result,
                wants_pasted_page=wants_pasted_page,
                wants_more_photos=wants_more_photos,
            )
        session = locked.session
    job.warnings = locked.warnings
    if kind.failure:
        monitoring.failed(
            kind.name,
            text,
            session=str(session.pk) if session else None,
            ad=session.name if session else None,
            job=str(job.pk),
            scene=scene if scene is not None else step.scene if step else None,
        )
    return posted


def record(session: Session, kind: Kind, text: str, level: Message.Level) -> None:
    """Record `text`, a notice of `kind` for the developer, with the session's job, or only
    in Sentry before the session has one, as there is no job to keep the warning with."""
    assert not kind.for_owner, "a notice for the shop owner is posted with post_notice"
    job = session.jobs.first()
    if job is None:
        monitoring.failed(kind.name, text, session=str(session.pk), ad=session.name)
    else:
        post_notice(job, kind, text, level)


def asked_since(job: Job, since: datetime) -> bool:
    """Whether the shop owner was asked, since `since`, for something the ad waits on: a
    notice of a kind that asks (Kind.asks)."""
    asking = {kind.name for kind in KINDS if kind.asks}
    return any(
        warning.get("kind") in asking and datetime.fromisoformat(warning["at"]) > since
        for warning in Job.objects.get(pk=job.pk).warnings
    )


def left_out_by_the_filter(pictures: Sequence[str], done: str) -> str:
    """The notice for work a model's safety filter refused until `pictures` were left out
    of it (requirement 22): which it went without, and why. `done` says what was done
    without them, such as "the ad was planned"."""
    named = pictures[0] if len(pictures) == 1 else f"{', '.join(pictures[:-1])} and {pictures[-1]}"
    return (
        f"The AI model's safety filter refused to look at {named}, so {done} without "
        f"{'it' if len(pictures) == 1 else 'them'}."
    )


def post_left_out_by_the_filter(job: Job, pictures: Sequence[str], done: str) -> None:
    """Record, with its warning line, that a model's safety filter refused
    work until `pictures` were left out of it (see left_out_by_the_filter). Told once: the
    same work made again past the same refusal, as on a redraw or a run again after a
    worker stopped, adds nothing."""
    text = left_out_by_the_filter(pictures, done)
    if all(warning["text"] != text for warning in job.warnings):
        post_notice(job, LEFT_OUT_BY_THE_FILTER, text, Message.Level.PROBLEM)


def refused_by_the_filter(what: str, then: str) -> str:
    """The notice for work a model's safety filter refused over `what` itself, which can't be
    left out of it (requirement 22): `then` says what happened instead, such as "it is kept
    unchecked"."""
    return f"The AI model's safety filter refused to look at {what}, so {then}."


def post_refused_by_the_filter(job: Job, what: str, then: str) -> None:
    """Record, with its warning line, that a model's safety filter refused to
    look at `what` (see refused_by_the_filter). Told once, as post_left_out_by_the_filter."""
    text = refused_by_the_filter(what, then)
    if all(warning["text"] != text for warning in job.warnings):
        post_notice(job, REFUSED_BY_THE_FILTER, text, Message.Level.PROBLEM)


def pictures_moved_to_azure() -> str:
    """The notice for a session whose pictures move to our Azure copy of the picture model,
    because OpenAI refused one for money (Julian 2026-10-29)."""
    return (
        "OpenAI refused to draw a picture because our OpenAI account is out of money (its "
        "spend limit or quota is reached), so this session's pictures are drawn on our Azure "
        "copy of the same picture model from now on. Azure draws at most "
        f"{settings.AZURE_PICTURES_PER_MINUTE} a minute, so pictures may take longer."
    )


def post_pictures_moved_to_azure(job: Job) -> None:
    """Record, with its warning line, that the session's pictures are drawn on
    Azure from now on (see pictures_moved_to_azure). Told once."""
    text = pictures_moved_to_azure()
    if all(warning["text"] != text for warning in job.warnings):
        post_notice(job, PICTURES_ON_AZURE, text, Message.Level.PROBLEM)
