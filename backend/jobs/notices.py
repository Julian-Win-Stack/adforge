"""Notices: what code records when a step falls back to a worse source or fails, so nothing
fails quietly, and what code tells the shop owner in the chat. The producer may leave a
failure out of its reply or soften it; a notice is written by code and says exactly what
happened.

Every notice is of a kind, and its kind decides who it is for (spec #272): the shop owner
sees a notice only when they must do something or it answers something they just did; every
other kind is for the developer only. Every notice is kept in `job.warnings`, and every
failure is sent to Sentry, grouped by its kind (adforge/monitoring.py)."""

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from adforge import monitoring
from chat import messages
from chat.models import Message, Session

from .models import Job, ProducedItem

logger = logging.getLogger(__name__)


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


def _developer(name: str, *, failure: bool = True) -> Kind:
    return Kind(name, for_owner=False, failure=failure)


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
TARGET_LENGTH_KEPT = _owner("target_length_kept", failure=False)
PRODUCER_STOPPED = _owner("producer_stopped", failure=False)

# #345: the owner must see when an original stranger's face can reach the ad.
FACE_SWAP_FAILED = _owner("face_swap_failed")
NO_PRESENTER_TO_SWAP = _owner("no_presenter_to_swap")

# What only the developer is shown: fallbacks, retries, refusals, setup and internal checks.
FACE_SWAP_INTERRUPTED = _developer("face_swap_interrupted")
FACE_SWAP_UNCHECKED = _developer("face_swap_unchecked")
RECOLOUR_FAILED = _developer("recolour_failed")
NO_COLOUR_REFERENCE = _developer("no_colour_reference")
CUT_OUT_FAILED = _developer("cut_out_failed")
CUT_OUT_TRIED_AGAIN = _developer("cut_out_tried_again")
# The side picker failed or was refused: the ad's picks go as redrawn cut-outs (#524).
SIDE_PICKER_FAILED = _developer("side_picker_failed")
# A scene agent's pick sent whole for want of a usable true crop (#524).
NO_TRUE_CROP = _developer("no_true_crop")
# A clean picture of an owner photo kept with its check giving no answer.
OWNER_PHOTO_UNCHECKED = _developer("owner_photo_unchecked")
PLACE_NOT_DRAWN = _developer("place_not_drawn")
# The closest failed picture filmed: the ad goes on, the owner has nothing to do.
CLOSEST_PICTURE_FILMED = _developer("closest_picture_filmed")
STEP_NOT_MADE = _developer("step_not_made")
STEP_WAITS_ON_A_QUESTION = _developer("step_waits_on_a_question")
STEP_NOT_WRAPPED_UP = _developer("step_not_wrapped_up")
SCENE_AGENT_GAVE_UP = _owner("scene_agent_gave_up")
SCENE_AGENT_NEEDS_CHOICE = _owner("scene_agent_needs_choice", asks=True)
FIX_NOT_CARRIED_ON = _developer("fix_not_carried_on")
LEFT_OUT_BY_THE_FILTER = _developer("left_out_by_the_filter")
REFUSED_BY_THE_FILTER = _developer("refused_by_the_filter")
END_PICTURE_UNCHECKED = _developer("end_picture_unchecked")
END_PICTURE_CLIP_REFUSED = _developer("end_picture_clip_refused")
FAILED_END_PICTURE_FILMED = _developer("failed_end_picture_filmed")
# A clip filmed after its move check failed MOST_MOVE_FAILS times in a run (physics try).
MOVE_CHECK_FILMED_ANYWAY = _developer("move_check_filmed_anyway")
# The move check couldn't answer, so the clip was filmed without it.
MOVE_UNCHECKED = _developer("move_unchecked")
# A starting picture and end picture filmed again after the clip judge failed their clip:
# the scene agent is told not to, and code no longer refuses it (rules review, Nov 9 2026).
SAME_PICTURE_FILMED_AGAIN = _developer("same_picture_filmed_again")
PICTURE_EVIDENCE_INCOMPLETE = _developer("picture_evidence_incomplete")
PAGE_PHOTOS_NOT_KEPT = _developer("page_photos_not_kept")
PHOTO_PICKER = _developer("photo_picker")
# A kept page photo still tiny after asking the shop for its full size.
TINY_PAGE_PHOTO = _developer("tiny_page_photo")
# How-to photos fill a scene agent's library room (#477), or more than the planner is shown.
HOW_TO_FILL_THE_ROOM = _developer("how_to_fill_the_room")
HOW_TO_NOT_ALL_SHOWN = _developer("how_to_not_all_shown")
PAGE_TEXT_NOT_COPIED = _developer("page_text_not_copied")
PASTE_NOT_CHECKED = _developer("paste_not_checked")
PAGE_VIDEO = _developer("page_video")
# A page's YouTube player, never downloaded on purpose (YouTube refuses cloud machines,
# docs/limitations.md): recorded, never a failure.
YOUTUBE_NOT_DOWNLOADED = Kind("youtube_not_downloaded", for_owner=False, failure=False)
TOO_MANY_PICTURES = _developer("too_many_pictures")
PIECE_NOT_SENT = _developer("piece_not_sent")
# A talking scene's packaging photo, for a box state, left out of its starting picture: the
# owner excluded it, the library no longer holds it (ticket #403), or the plan named none.
PACKAGING_PHOTO_NOT_SENT = _developer("packaging_photo_not_sent")
# A starting picture drawn without the plan's product photos: none of them can be picked.
NO_PRODUCT_PHOTO_TO_PICK = _developer("no_product_photo_to_pick")
# A changed scene the cohesion check found not matching the others, let through as its
# refusals ran out, or a check that gave no answer.
SCENE_COHESION_LET_THROUGH = _developer("scene_cohesion_let_through")
# A change to one scene after planning that newly breaks a whole-plan rule, let through
# instead of refused (jobs.plan_rules.STILL_REFUSE_A_SCENE_CHANGE says which still refuse).
PLAN_RULE_LET_THROUGH = _developer("plan_rule_let_through")
# A new plan kept though it breaks a rule the planner is only told, never sent back for
# (jobs.planning.rules_not_kept): a rule that comes back broken may earn a check again.
PLAN_RULE_NOT_KEPT = _developer("plan_rule_not_kept")
NO_FRONT_PHOTO = _developer("no_front_photo")
NO_FIRECRAWL = _developer("no_firecrawl")
FIRECRAWL_FAILED = _developer("firecrawl_failed")
# A page read again, fresh, because its first read couldn't be used, as a shop's bot check.
PAGE_READ_AGAIN = _developer("page_read_again")
PICTURES_ON_AZURE = _developer("pictures_on_azure")
JUDGE_FIRST_QUESTION = _developer("judge_first_question")
CLIP_DESCRIPTION_UNAVAILABLE = _developer("clip_description_unavailable")
PART_CHECK_UNCHECKED = _developer("part_check_unchecked")
PART_TRIED_AGAIN = _developer("part_tried_again")
PHOTO_NOT_LABELLED = _developer("photo_not_labelled")
LABELLED_FOR_THE_PICKER = _developer("labelled_for_the_picker")
# Page photos the picker saw only small, labelled again from their own files; and one whose
# labelling failed, so the picker's guess stands.
LABELLED_FROM_FILE = _developer("labelled_from_file", failure=False)
PICKER_LABEL_KEPT = _developer("picker_label_kept")
PLANNER_NOT_SHOWN = _developer("planner_not_shown")
PLANNER_QUESTION_TIMEOUT = _developer("planner_question_timeout")
# The planner asked again while its owner question still waits: the producer is told to wait,
# and code no longer refuses it (rules review, Nov 9 2026).
PLANNED_WHILE_QUESTION_WAITS = _developer("planned_while_question_waits")
NO_PHOTO_FOR_A_PICTURE = _developer("no_photo_for_a_picture")
CARD_PICTURE = _developer("card_picture")
LINE_TO_CAMERA = _developer("line_to_camera")
PLANNING_CHECK_HANDED_BACK = _developer("planning_check_handed_back")
# The plan quality check (#404): every verdict is recorded so Julian can grade the checker,
# never a failure; a check that gave no answer, and a plan kept after every try got a "no",
# are fallbacks.
PLAN_QUALITY_VERDICT = Kind("plan_quality_verdict", for_owner=False, failure=False)
PLAN_QUALITY_UNANSWERED = _developer("plan_quality_unanswered")
PLAN_QUALITY_KEPT = _developer("plan_quality_kept")
DEFAULT_AD_SHORTENED = _developer("default_ad_shortened")
TOOL_WITHOUT_A_WHY = _developer("tool_without_a_why")
# An agent's tool raised: an outside service failed (a refused H3 render, say) or code broke.
TOOL_FAILED = _developer("tool_failed")
# The producer's make_picture made no picture: it failed its check, or couldn't be checked.
MADE_PICTURE_FAILED = _developer("made_picture_failed")
# ffmpeg couldn't join the clips and music into the ad: the producer is told it failed.
ASSEMBLY_FAILED = _developer("assembly_failed")
ANSWER_BROKE_A_RULE = _developer("answer_broke_a_rule")
FACE_NOT_NOTED = _developer("face_not_noted")
# No product photo could go on the end card (spec #401): it is drawn without one.
END_CARD_WITHOUT_PHOTO = _developer("end_card_without_photo")
# A clip's transcript has words but no usable time for its last one, so the clip plays to its
# end, any silence after the line kept (#442).
CLOSING_SILENCE_NOT_CUT = _developer("closing_silence_not_cut")
PRODUCER_NUDGED = _developer("producer_nudged")
PRODUCER_RETRIED = _developer("producer_retried")
PRODUCER_GAVE_UP = _developer("producer_gave_up")
# A second copy of a producer or scene step task, sent while another copy works, quit as it
# started, so nothing ran or was paid for twice. Expected while the queue is backed up, so
# recorded, never a failure.
SECOND_COPY_QUIT = Kind("second_copy_quit", for_owner=False, failure=False)
# The voice account was full, so an idle ad's voices were deleted (spec round-7-other-reds 2a).
VOICE_FREED = _developer("voice_freed")

KINDS = tuple(kind for kind in globals().values() if isinstance(kind, Kind))


def post_once(job: Job, kind: Kind, text: str) -> None:
    """Post `text`, a notice of `kind`, in red, unless the job was told exactly that."""
    job.refresh_from_db(fields=["warnings"])
    if all(warning["text"] != text for warning in job.warnings):
        post_notice(job, kind, text, Message.Level.PROBLEM)


def post_notice(
    job: Job,
    kind: Kind,
    text: str,
    level: Message.Level,
    carrying: Sequence[messages.AttachedFile] = (),
    step: messages.StepSignal | None = None,
    suggested: messages.SuggestedNote | None = None,
    tries_of: int | None = None,
    watch_clip: ProducedItem | None = None,
    owner_question: int | None = None,
    pasted_page_result: str = "",
    wants_pasted_page: bool | None = None,
    wants_more_photos: bool = False,
    scene: int | None = None,
    shown_as: Message.Level | None = None,
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
    one asking the owner to watch a clip names `watch_clip`;
    one answering the page panel's message carries its `pasted_page_result`, and one keeping
    a pasted page with too few photos `wants_more_photos`. A failure the owner is only asked to
    look at is shown grey, `shown_as` info, while its warning keeps `level`, so the run
    scripts still count it red. Gives the message posted, or None for a notice only recorded
    or a job with no chat."""
    shows = carrying or suggested or tries_of or watch_clip or owner_question or pasted_page_result
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
                level=shown_as or level,
                carrying=carrying,
                step=step,
                suggested=suggested,
                tries_of=tries_of,
                watch_clip=watch_clip,
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


def record(
    session: Session, kind: Kind, text: str, level: Message.Level, scene: int | None = None
) -> None:
    """Record `text`, a notice of `kind` for the developer, with the session's job, or only
    in Sentry before the session has one, as there is no job to keep the warning with (only
    the server log, for a kind that isn't a failure)."""
    assert not kind.for_owner, "a notice for the shop owner is posted with post_notice"
    job = session.jobs.first()
    if job is None:
        # Nowhere to keep it but Sentry, which only takes failures.
        if kind.failure:
            monitoring.failed(
                kind.name, text, session=str(session.pk), ad=session.name, scene=scene
            )
        else:
            logger.info("%s in session %s: %s", kind.name, session.pk, text)
    else:
        post_notice(job, kind, text, level, scene=scene)


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


def ask_to_watch(clip: ProducedItem, text: str) -> None:
    """Ask once per clip and reason, linking the small chat line to that exact clip."""
    scene = clip.scene
    assert scene is not None, "a watch notice names a scene's clip"
    if clip.watch_notices.filter(text=text).exists():
        return
    post_notice(
        scene.job,
        CHECK_IT_YOURSELF,
        text,
        Message.Level.INFO,
        scene=scene.number,
        watch_clip=clip,
    )
