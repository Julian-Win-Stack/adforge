"""The producer: the agent the user talks to. What it is told to do, and its tools."""

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.db import IntegrityError, transaction
from django.db.models import Max, Q, QuerySet, Sum
from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator

from adforge.retry import OutsideServiceDown
from chat import messages
from chat.models import Attachment, Message, Session
from gateway.models import ModelCall
from gateway.types import BlockedBySafetyFilter, MusicHandoff, UnusableReply
from jobs import (
    notices,
    owner_answers,
    owner_parts,
    page,
    plan_edits,
    scene_references,
    stale,
)
from jobs.assembly import AssemblyFailed, AssemblyTimedOut, website_of
from jobs.library import library_of
from jobs.library_items import add_kept_photos, library_line
from jobs.made_pictures import PictureNotMade, make_picture
from jobs.models import (
    Job,
    OwnerQuestion,
    ProducedItem,
    ProductPhoto,
    ReferencePhoto,
    Scene,
    SceneStep,
)
from jobs.notices import post_notice
from jobs.plan_rules import ProductState, TalkingStyle
from jobs.planning import (
    BrollKind,
    Part,
    PersonGender,
    PersonShown,
    ScenePicture,
    ScenePiece,
    packaging_photo_number,
    pictures_missing,
    piece_missing,
)
from jobs.product_videos import save_videos
from jobs.reference_photos import ReferencePhotoUnusable
from jobs.watcher import watch_videos
from jobs.work import (
    DECLARED_INSTEAD,
    LineTooShort,
    NoPlaceYet,
    NoPresenterYet,
    NoSuchVersion,
    SceneBusy,
    SkippedPhoto,
    TryNotOnOffer,
    assemble_ad,
    copy_and_pick,
    create_music,
    create_person,
    drawn_for,
    edit_scene_place,
    go_back,
    go_back_is_free,
    keep_page,
    keep_the_clip_before_it_failed,
    kept_before_it_failed,
    latest,
    moments_by_video,
    music_mood,
    music_prompt,
    music_seconds,
    new_scene_place,
    note_face,
    person_shown_for,
    plan,
    read_and_check_page,
    remake_person,
    run_checks,
    save_photos,
    show_once,
    tries_shown,
    try_name,
    use_music_again,
    use_try,
    versions,
    why_the_checks_havent_passed,
    why_the_checks_passed,
)

from . import (
    hard_list,
    loop,
    note_photos,
    planning_questions,
    snapshot,
    step_signals,
    voice_changes,
)
from .look import Look
from .loop import Agent, Failed, Refused, Tool
from .models import ToolCall
from .money import dollars
from .scene_agent.lifecycle import PLACE_NAMES
from .scene_tools import PassNote, StartSceneAgent

LEGACY_FIXING_AGENT = "fixing_agent"

# What the chat shows the shop owner when the product page can't be read: plain words, as
# the technical reason is the producer's, in its tool's result.
PAGE_NOT_OPENED = (
    "I couldn't open your product page from its link. Paste its text and photos in the panel "
    "below, or send another link."
)
PAGE_TEXT_UNUSABLE = "Your product page opened, but I can't use its text."
# What follows why, when the page opened but its text can't be used.
PASTE_OR_LINK = "Paste its text and photos in the panel below, or send another link."
# What the producer is told when the product page can't be read, after why: the chat has asked
# the shop owner for the page panel (ADR 0006).
PASTE_IN_THE_PANEL = (
    "The chat has shown the shop owner why and opened a panel under it where they can paste "
    "the page's text and attach its photos. Tell them in one line that the page can't be read, "
    "and that they can paste its text and attach its photos in the panel below, or send a "
    "working link to the product's own page."
)
# What the shop owner is told when a page is read while the page panel is open.
PANEL_NOT_NEEDED = "The page from your new link was read, so there's no need to paste it."

INSTRUCTIONS = """\
You are the producer at AdForge. You make short vertical video ads for one product at a \
time, in a chat with the shop owner who sells it. In each ad a person speaks to camera, \
one line per scene. Some scenes show the product instead, while the person's voice says \
the line over it: the plan says which. A scene agent owns every scene. H3 speaks the exact
checked line in each clip.
Never tell the shop owner which scenes are which, or that there are two kinds.
You work by calling tools. Call one when you need it, read what it hands back, and decide \
what to do next. Before a tool that takes a while, say in one short sentence what you're \
about to do. When there is nothing left to do, reply to them; when you need the shop \
owner, ask them with ask_owner.
The usual order is: read the page, plan the ad, create the person, run the planning
checks, create the music, and call start_scene_agent for every scene once its presenter
and checked line exist. Start all ready scenes together in one turn, without waiting for
one to finish before starting another. Give each scene your own instruction and exact
chunks of the owner's messages in quotes. Code checks those quotes; if refused, quote
their words exactly. The scene agent draws its starting picture and films, checks and
fixes in its same chat. Code draws the shared room and presenter pictures before filming.
The starting picture needs no owner's OK, except the existing owner-part approval gate.
Once every scene is finished, assemble the ad. When the planning checks hand back a failure,
use the scene tools to replace or remove unsupported claims or split long lines, then check
again; never ask the shop owner to resolve a check. Never assemble while a scene agent is
working; wait until you are told how its latest run ended and what it cost. When a
tool hands back something to ask the shop owner, ask it with ask_owner and wait for their \
answer before passing their choice to a tool.
The plan can change at any step: edit_scene changes any of a scene's fields, and \
add_scene, remove_scene and reorder_scenes change which scenes play and in what order. \
Each says which steps the change made stale; nothing is made again by itself. Run the \
planning checks on what changed, make again only the stale steps the change needs with the \
scene tools, then assemble the ad. Once it is shown, tell the shop owner in one line what \
changed and what it cost.
Every version of a scene is kept. use_version puts an earlier version back in the ad for \
nothing, and keep_try puts in one of the kept tries of a clip; once an ad \
has been shown, each assembles it again. The shop owner can put a version in the ad \
themselves from the scene's card: the app swaps it, assembles the ad again and tells you, \
so don't swap it again. create_music again in another mood makes new \
music for the ad; assemble the ad again to put it in. cost_so_far says what the ad has \
cost so far, by part. When you tell the shop owner what the ad has cost in all, write \
[total cost] where the figure goes, never a figure of your own: the app puts in the exact \
total as your message is sent, your own turns included.
The person and the place can change at any step too: remake_person changes their look,
voice words or both. No voice sample exists: the owner hears the voice in the clips.
A voice change must refilm every scene because its voice is baked into the clip.
Call remake_person to quote that cost first; it shows the owner how to confirm. After the
owner's full confirmation call it again with cost_quote and confirmation, then refilm every
scene and assemble. Never take a question, silence or a yes to something else as consent.
For a new look, your instruction goes on top of the standard portrait prompt.
set_place gives a scene a place of its own, drawn new or a small edit of the one it has.
Each says which steps the change made stale. Look at a new portrait or place with look
before making anything from it.
Scene tools start their work in the background and hand back at once, before anything is \
made. Tell the shop owner the work has started, then carry on or reply: don't call the tool \
again to see whether it has finished. You are told in a message from the system, not from \
the shop owner, when a step fails, and once no step is still running, of every step that \
finished meanwhile. Then tell the shop owner what was made. \
When it failed, take a way on (make it again, or another way the message names) and tell \
the shop owner only what they can act on, as said below.
The shop owner can press pause. A message from the app then says which steps it cancelled \
before they started, with what each was for, and which are still running and will finish. \
Start nothing while paused: every tool that costs money answers paused. If the shop owner hasn't \
written since, tell them in one line that you paused and what is still finishing, then \
wait. Their next message ends the pause: act on it, and call again the tools still needed. \
Before the first plan, call plan_ad to collect every photo, size, price, in-use \
photo and demo question together. Show the planner's numbered question once with ask_owner, \
keeping every stated default. Any owner message answers this one round: plan again, use \
their answers and the defaults for anything unanswered, and never ask it again. When code \
says the approved waiting limit passed, plan with the stated defaults. A question about \
what the page contradicts keeps waiting: it has no no-answer default. Page unreadable \
and no product photo at all still stop and ask before planning. Other approval gates \
still need their yes.
Every fact about the product comes from its page or from the shop owner. You have no tool \
that searches the web, so never look anything up, infer or guess. When something you need \
is missing or unclear before the plan, have plan_ad collect the narrow question with \
every other question first. When you can't do something, say so plainly and say why.
You ask the shop owner a question with ask_owner, which shows it in the chat and ends your \
turn once the turn's other calls are done: their next message is the answer. In the same \
turn, start everything else that can go on meanwhile, and don't repeat the question in \
your own words: the chat shows it once. Ask only when the ad can't go on without them: \
their yes to the picture of a part no photo shows, something the ad needs that neither the \
page nor the library holds, or what a tool handed back for them to answer. Everything else \
you answer from what you already have, and go on.
When a product page can't be read, the chat opens a panel where the shop owner can paste \
the page's text and attach its photos. If they paste a page's text into the chat instead, \
don't take facts from it: point them to the panel, which keeps it as the page.
A failure a tool or step hands back is yours to work around, not news for the shop owner: \
the chat shows them only what they can act on. Tell them of a failure only when it needs \
something from them (a photo, a page, an answer, their choice), and then in plain words for \
someone who isn't technical: no AI model, service or tool names, no library item numbers, \
no "something went wrong on our side". Say what you need and what happens next. When the ad \
can't go on at all, say so plainly and why.
A scene may show a part of the product that no photo shows, drawn from the shop owner's \
words, or from a description made up for it when they let us draw it. The part gets a \
picture of its own, shown to them in the chat, and the clip of a scene showing it isn't \
made until they say yes to that picture: ask them with ask_owner, and wait. Only when \
they say yes to that picture in their own words, call approve_owner_part. When they say \
what to change or send a photo of the part, call correct_owner_part, which draws the \
part's picture again and shows it, and ask them again. Never take silence, a question or \
a yes to something else as their yes, and never decide for them. Every other scene goes \
on meanwhile.
You can't see pictures or videos yourself. When you need to know what a scene's clip, an \
earlier version, a library picture or a try of a fix shows, call look with one question: it \
is shown them, with what the clip was sent, and tells you in words what it sees, what is \
wrong, its root cause and a direction. It decides nothing: you do. Before \
the ad is planned, every photo they send is a product photo: if they send one of something \
else, don't add it, and tell them to send it with a note on a scene once the first version \
is made. Once the ad is planned, put a photo or a video they send into the library with \
add_to_library. A photo or video sent for a scene belongs in the library and in that scene's note.
When the owner's note concerns one or more scenes, call pass_note
once with each scene's own instruction and exact owner quotes. Include words applying
to every scene as well as that scene's own words; respect corrections across messages.
Pass the note even while an agent works: it opens a fresh run in its same chat, remembers
earlier work and reads the note before its next tool. Never take a later thanks as the
note. Use pass_note for an idle scene too, and after the pause is released.
Slower can't be done: every clip plays at real-time speed.
The scene agent picks its photos; do not set its photo references. set_references keeps
owner exclusions in exclude and chooses a page-video moment in piece, or removes it with
no_piece. The agent looks at that moment's frames and decides what to take from it.
When a scene agent asks for a photo or video, code asks the owner once and tells you.
Do not repeat the question or wait: the ad keeps moving. Hand a photo or video answer
back through pass_note with your instruction and the owner's checked quotes.
You are told when the latest run passes, gives up or reaches its cap, with its cost.
Tell the owner in one line what changed and what it cost. On a give-up explain the
reason plainly and offer the way out its notice gives: for B-roll, an owner photo, or a
talking scene only while another B-roll scene remains (in a middle scene "talking while doing
it", using it on herself); for talking, an owner photo or a different line.
A part of the product drawn from the shop owner's words or made up is settled once they \
say yes to its picture: never ask them for a photo of it or how it looks again.
make_picture makes a new picture for the ad's library from your prompt and library items. \
Send single pictures first. Make a combined picture, such as several sides of the product \
in one, only when a scene's references left out a picture the scene needs. Look at what \
you made, with look on its library item, before you send it.
Keep your messages short and friendly, written for someone who isn't technical."""


class ReadPage(Tool):
    """Start a job for an ad, from the link to the product's page. Reads the page and keeps
    its text and its product photos: everything the ad says and shows comes from them. Says
    which photos were skipped and why."""

    name = "read_page"

    link: str = Field(description="The link to the product's page, as the shop owner gave it.")
    target_seconds: int | None = Field(
        description="How long the shop owner wants the ad to be, in seconds, or null if they "
        "didn't say."
    )

    # Validators rather than max_length or ge, which OpenAI's strict schema doesn't accept.
    @field_validator("link")
    @classmethod
    def _a_link_to_a_page(cls, link: str) -> str:
        if len(link) > 2000:
            raise ValueError("it is over 2,000 characters, too long to be stored")
        try:
            URLValidator(schemes=["http", "https"])(link)
        except ValidationError:
            raise ValueError(f'"{link}" isn\'t a link to a web page') from None
        return link

    @field_validator("target_seconds")
    @classmethod
    def _a_length_that_can_be_stored(cls, seconds: int | None) -> int | None:
        if seconds is not None and not 1 <= seconds <= 32_767:
            raise ValueError("the length has to be from 1 to 32,767 seconds")
        return seconds

    def run(self, call: ToolCall) -> str:
        job = _the_job(call)
        # Before anything is read again. A page that wasn't read, with no product photo kept,
        # had nothing made from it: a new link is read for the same ad.
        if job is not None and _page_read(job):
            hard_list.refuse_another_product(job, self.link)
        if job is None:
            job = Job.objects.create(
                session=call.session, product_url=self.link, target_seconds=self.target_seconds
            )
            call.job = job
            call.save(update_fields=["job"])
        elif _page_read(job):
            # A read that stopped once the page was kept may have left photos out of the
            # job's library: they join it now.
            add_kept_photos(job)
            return (
                f"{self.link} was already read for this ad, so nothing was read or paid for "
                f"again. Reading it cost {dollars(_spent(job, self.name))}. The ad has "
                f"{_photos(job.photos.filter(from_video=None).count())}."
            )
        else:
            # The page before wasn't read successfully, so nothing has been made from it: the
            # ad is the same one, from this link, and has no page until this one is read.
            job.product_url = self.link
            job.target_seconds = self.target_seconds
            job.page_text = ""
            job.page_text_full = ""
            job.page_html_key = ""
            job.status = Job.Status.READING_PAGE
            job.save(
                update_fields=[
                    "product_url",
                    "target_seconds",
                    "page_text",
                    "page_text_full",
                    "page_html_key",
                    "status",
                ]
            )
        try:
            checked = read_and_check_page(job, self.link)
        except page.PageUnreadable as error:
            post_notice(
                job,
                notices.PAGE_UNREADABLE,
                PAGE_NOT_OPENED,
                Message.Level.PROBLEM,
                wants_pasted_page=True,
            )
            raise Failed(f"the page couldn't be read: {error} {PASTE_IN_THE_PANEL}") from error
        read, product_page, check = checked.read, checked.product_page, checked.check
        download = read.download
        if check.decision == "unreadable":
            post_notice(
                job,
                notices.PAGE_UNREADABLE,
                f"{PAGE_TEXT_UNUSABLE} {check.reason} {PASTE_OR_LINK}",
                Message.Level.PROBLEM,
                wants_pasted_page=True,
            )
            raise Failed(
                f"the page was read but can't be used: {check.reason} {PASTE_IN_THE_PANEL}"
            )
        # Before the photos: a page whose text can't be copied isn't read, and keeps nothing.
        # The photos are picked while the text is copied.
        try:
            text, picked = copy_and_pick(job, read, product_page)
        except (UnusableReply, OutsideServiceDown, BlockedBySafetyFilter) as error:
            raise Failed(
                f"picking this product's own text out of the page failed ({error}), so the "
                "page wasn't read. Tell the shop owner they can ask to read it again."
            ) from error
        for notice, level in picked.notices:
            post_notice(job, notices.PHOTO_PICKER, notice, level)
        # The product's colour list, before any photo is labelled from it (#273 point 2).
        if picked.colours:
            job.colours = list(picked.colours)
            job.save(update_fields=["colours"])
        # Without Firecrawl set up nothing is picked off the page, which is a setup mistake
        # the notice already names, not a doubt about this page's photos.
        declared = picked.urls is None and read.marked is not None
        if picked.urls is None:
            skipped = save_photos(job, product_page.photo_urls)
        else:
            skipped = save_photos(
                job,
                picked.urls,
                copies=picked.copies,
                labels=picked.labels,
                faces=picked.faces,
                unnoted=picked.unnoted,
                as_given=picked.as_given,
            )
            if skipped and not job.photos.exclude(source_url="").exists():
                post_notice(
                    job, notices.PAGE_PHOTOS_NOT_KEPT, _none_kept(skipped), Message.Level.PROBLEM
                )
                skipped = save_photos(job, product_page.photo_urls)
                declared = True
            elif skipped:
                post_notice(
                    job,
                    notices.PAGE_PHOTOS_NOT_KEPT,
                    _not_downloaded(skipped),
                    Message.Level.PROBLEM,
                )
        # The job's library starts from the page's photos, labelled, before the videos' look
        # frames join it.
        add_kept_photos(job, product_text=text)
        # The gallery videos the picker picked, each watched for its moments, look frames,
        # colour and words (jobs.watcher). One that can't be watched is told in red.
        save_videos(job, list(picked.videos), page_url=download.final_url)
        unread_videos = watch_videos(job, text)
        # Only once every photo is kept: a worker that stops part-way through them leaves the
        # page unread, so the read run again keeps them all rather than the few already kept.
        keep_page(job, download, product_page, text)
        if messages.page_panel_wanted(call.session):
            post_notice(
                job,
                notices.PANEL_CLOSED,
                PANEL_NOT_NEEDED,
                Message.Level.INFO,
                wants_pasted_page=False,
            )
        # The pictures cut from its videos are the planner's, not news to the owner.
        kept = job.photos.filter(from_video=None).count()
        if kept == 0:
            post_notice(
                job,
                notices.NO_PRODUCT_PHOTO,
                "I couldn't find a photo of your product on the page. Attach one and I'll make "
                "the ad.",
                Message.Level.PROBLEM,
            )
        told = [f"Started job {job.pk} and read {download.final_url}. {check.reason}"]
        if download.final_url != self.link:
            told.append(f"The link led to {download.final_url}, so that is the page read.")
        told.append(f"Kept {_photos(kept)}.")
        told += unread_videos
        if skipped:
            told.append(f"Skipped {len(skipped)} photo{'s' if len(skipped) != 1 else ''}:")
            told += [f"- {photo.url}: {photo.reason}" for photo in skipped]
        if kept == 0:
            told.append(
                "Every scene is made from a product photo, so ask the shop owner to attach "
                "at least one."
            )
        job.photo_worry = _doubtful(picked.blocked, declared, kept) if kept else ""
        job.save(update_fields=["photo_worry"])
        if job.photo_worry:
            told.append(
                f"The photos are in doubt: {job.photo_worry} Call plan_ad now: the planner "
                "will ask about them with every other question in one message."
            )
        return "\n".join(told)


class PhotoShows(BaseModel):
    """What one of the shop owner's photos shows, from what they said about it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    photo: int = Field(
        description='The photo\'s number, as the chat gives it: "owner photo 3" is 3.'
    )
    shows: Literal["the product", "something else"] = Field(
        description='"the product" when it shows the product the ad sells; "something else" '
        "when they said it shows their own thing for the ad, such as their toilet or their "
        "kitchen. If their words don't say, ask them before you call this."
    )


class UsePhotos(Tool):
    """Add the product photos the shop owner attached to their messages in this chat to the
    job, after the page's own. Say what each of their photos shows: only those of the product
    are added. Use it when the page had no usable photos or they want their own used."""

    name = "use_photos"

    photos: list[PhotoShows] = Field(
        description="Every photo the shop owner attached in this chat, each once."
    )

    def run(self, call: ToolCall) -> str:
        job = _with_its_page(call, "the photos belong to its ad")
        if job.scenes.exists():
            raise Refused(
                "the ad is already planned, so its product photos can't change now. A photo of "
                "the shop owner's own thing goes with a note on a scene, once its clip is made."
            )
        attached_photos = messages.owner_photos(call.session)
        said = {each.photo: each.shows for each in self.photos}
        unsaid = [number for number in range(1, len(attached_photos) + 1) if number not in said]
        if len(said) != len(self.photos) or set(said) - set(range(1, len(attached_photos) + 1)):
            raise Refused(
                f"the chat has {_owner_photos(len(attached_photos))}: say what each shows, "
                "once, by its number."
            )
        if unsaid:
            raise Refused(
                "say what each of the shop owner's photos shows: you left out owner photo"
                f"{'s' if len(unsaid) > 1 else ''} {listed_numbers(unsaid)}."
            )
        others = [number for number, shows in said.items() if shows == "something else"]
        kept = set(job.photos.values_list("file", flat=True))
        position = job.photos.aggregate(last=Max("position"))["last"] or 0
        added = 0
        for number, attached in enumerate(attached_photos, 1):
            if attached.file in kept or number in others:
                continue
            position += 1
            added += 1
            # The shop owner's photo was never seen by the picker, so it is noted on its own.
            ProductPhoto.objects.create(
                job=job,
                position=position,
                file=attached.file,
                has_face=note_face(job, attached.file, attached.file),
            )
        # Each joins the job's library at the next number, after the page's photos.
        add_kept_photos(job)
        count = job.photos.filter(from_video=None).count()
        told = f"Added {added} of the shop owner's photos. The job now has {_photos(count)}."
        kept_before = [number for number in others if attached_photos[number - 1].file in kept]
        others = [number for number in others if number not in kept_before]
        if kept_before:
            told += (
                f" Owner photo{'s' if len(kept_before) > 1 else ''} "
                f"{listed_numbers(kept_before)} {'were' if len(kept_before) > 1 else 'was'} "
                "already added as a product photo before, and can't be taken out now: tell "
                "the shop owner so."
            )
        if others:
            # Before the plan every photo is a product photo, so one of their own thing would
            # be planned as the product (Julian 17:55; test C9).
            told += (
                f" Owner photo{'s' if len(others) > 1 else ''} {listed_numbers(others)} "
                f"show{'' if len(others) > 1 else 's'} something else than the product, so "
                f"{'they weren' if len(others) > 1 else 'it wasn'}'t added: tell the shop owner "
                "it can't be used yet, and to send it with a note on a scene once the first "
                "version is made. Plan the ad without it."
            )
        return told


def _owner_photos(count: int) -> str:
    return (
        "no owner photo"
        if count == 0
        else f"owner photos 1 to {count}"
        if count > 1
        else ("owner photo 1")
    )


def listed_numbers(numbers: list[int]) -> str:
    said = [str(number) for number in numbers]
    return said[0] if len(said) == 1 else f"{', '.join(said[:-1])} and {said[-1]}"


class PlanAd(Tool):
    """Plan the ad from the product page, its photos and everything said in this chat: the
    scenes and each one's line, the product's colour and the photos that show it, and the
    person who presents it. If the planner needs the shop owner to settle something first,
    says what to ask them."""

    name = "plan_ad"

    def run(self, call: ToolCall) -> str:
        job = _with_its_page(call, "the ad is planned from it")
        if job.scenes.exists():
            planned = job.model_calls.filter(
                purpose="plan_ad", outcome=ModelCall.Outcome.SUCCEEDED, decision="plan"
            ).last()
            assert planned is not None
            return (
                "The ad was already planned, so nothing was planned or paid for again. "
                f"Planning it cost {dollars(_spent(job, self.name))}.\n"
                f"{_the_plan(job, planned.reason)}"
            )
        if not job.photos.filter(from_video=None).exists():
            raise Refused(
                "the ad has no product photos yet, and every scene is made from one. Ask the "
                "shop owner to attach at least one, then add it with use_photos."
            )
        from .planning_questions import may_plan, record_question

        may_plan(job, call.session)
        decision = plan(job)
        if decision.question is not None:
            call.asked_about = "the plan"
            record_question(call, decision)
            return (
                f"The ad can't be planned until the shop owner answers: {decision.question} "
                f"Why: {decision.reason} Show this numbered message once through ask_owner, "
                "including every stated default. Plan again once they have answered; "
                "unanswered items take their stated defaults, with no second question. "
                "If they answer with a photo, add it with use_photos before planning again."
            )
        return _the_plan(job, decision.reason) + _parts_drawn(job)


def _parts_drawn(job: Job) -> str:
    """Draw the picture of each part of the product the plan shows that no photo shows, each
    shown to the shop owner (spec #210 point 20), and say what the producer does next."""
    said = ""
    tried: list[object] = []
    for scene in job.scenes.filter(owner_part__isnull=False).order_by("number"):
        part = owner_parts.owner_part(scene)
        # One drawn, or tried, for an earlier scene holding the same part is this one's too.
        if part is None or scene.owner_part in tried or owner_parts.picture_is_current(scene):
            continue
        tried.append(scene.owner_part)
        said += "\n" + _part_drawn(scene, part, "The clip of every scene showing it isn't made")
    return said


class CreatePerson(Tool):
    """Create the person who presents the ad: a portrait and shared voice words.
    The portrait is shown now; the voice is heard in the clips, with no advance sample."""

    name = "create_person"

    def run(self, call: ToolCall) -> str:
        job = _the_job(call)
        if job is None or not job.scenes.exists():
            raise Refused("the ad hasn't been planned yet, and the person is made from the plan.")
        portrait = create_person(job)
        if not _shown_before(call.session, portrait.file):
            messages.add(
                call.session,
                role=Message.Role.AGENT,
                carrying=[messages.AttachedFile(Attachment.Kind.PICTURE, portrait.file)],
            )
        return (
            "The person is made and their portrait is shown. "
            "Their voice will be heard in the clips."
        )


class RemakePerson(Tool):
    """Make the presenter again: their look, voice words, or both. New descriptions replace
    the plan's description. A new look draws and shows a portrait; the instruction goes on
    top of its standard prompt. A voice change first quotes the cost of filming every scene
    again and shows the owner how to confirm; after their full confirmation, give cost_quote
    and confirmation. No advance voice sample is made. Nothing is filmed until you call the
    scene agents. Look at a new portrait before using it."""

    name = "remake_person"

    remake: Literal["look", "voice", "both"] = Field(
        description='What to make again: "look" for a new portrait, "voice" for new voice words, '
        '"both" for both.'
    )
    gender: PersonGender | None = Field(
        default=None,
        description="What the person presents as, when it changes; it goes into both the "
        "portrait and the voice, so remake both. Null keeps it.",
    )
    looks: str | None = Field(
        default=None,
        description="How the person looks, in full, replacing the plan's description, when the "
        "look is made again. Null keeps it.",
    )
    voice: str | None = Field(
        default=None,
        description="How the person's voice sounds, in full, replacing the plan's description, "
        "when the voice is made again. Null keeps it.",
    )
    instruction: str | None = Field(
        default=None,
        description="Your detailed instruction for the portrait or the new voice words, "
        "on top of the standard prompt, such as what the shop owner asked for. Null for none.",
    )

    cost_quote: int | None = Field(
        default=None,
        description="The earlier remake_person cost quote to approve; null requests a quote.",
    )
    confirmation: str | None = Field(
        default=None,
        description="The owner's full later confirmation message, exactly as written; "
        "never an extracted yes.",
    )

    def run(self, call: ToolCall) -> str:
        if completed := voice_changes.completed(call):
            return completed
        job = _the_job(call)
        if job is None or latest(job, ProducedItem.Kind.PORTRAIT) is None:
            raise Refused("the person hasn't been made yet: create the person first.")
        look = self.remake in ("look", "both")
        voice = self.remake in ("voice", "both")
        if self.looks is not None and not look:
            raise Refused('new looks are drawn in a new portrait: remake "look" or "both".')
        if self.voice is not None and not voice:
            raise Refused('a new voice description needs a new voice: remake "voice" or "both".')
        if self.gender is not None and self.gender != job.person_gender and self.remake != "both":
            raise Refused(
                'a new gender is both seen and heard: remake "both", so the portrait and the '
                "voice match."
            )
        changed: dict[str, Any] = {
            field: value.strip() if isinstance(value, str) else value
            for field, value in (
                ("person_gender", self.gender),
                ("person_looks", self.looks),
                ("person_voice", self.voice),
            )
            if value is not None
        }
        if voice:
            if (
                not self.instruction
                and changed.get("person_voice", job.person_voice) == job.person_voice
                and changed.get("person_gender", job.person_gender) == job.person_gender
            ):
                raise Refused(
                    "describe the new voice in full, or give the instruction that changes it."
                )
            proposal = self.model_dump(exclude={"cost_quote", "confirmation"})
            quote = voice_changes.consent(
                job, call, proposal, quote_id=self.cost_quote, confirmation=self.confirmation
            )
            if quote is not None:
                return quote
            if self.instruction:
                changed["person_voice"] = (
                    f"{changed.get('person_voice', job.person_voice)}. {self.instruction.strip()}"
                )
        # Made from the person as described now; the plan keeps the new description only once
        # they are made, so a failed remake leaves it describing the person the ad shows.
        for field, value in changed.items():
            setattr(job, field, value)
        portrait = remake_person(job, look=look, instruction=self.instruction or "", call=call)
        shown = (
            [messages.AttachedFile(Attachment.Kind.PICTURE, portrait.file)]
            if portrait is not None and not _shown_before(call.session, portrait.file)
            else []
        )
        with transaction.atomic():
            if changed:
                Job.objects.filter(pk=job.pk).update(**changed)
            if shown:
                messages.add(call.session, role=Message.Role.AGENT, carrying=shown)
            voice_changes.checkpoint(call, voice_changed=voice, text="")
            if voice:
                job.scenes.update(status=Scene.Status.PLANNED)
            result = (
                "Changed the H3 person. "
                + ("The new portrait is made. " if look else "")
                + (
                    "The voice words are kept; no voice sample is made. "
                    "Refilm every scene with start_scene_agent, then assemble the ad. "
                    if voice
                    else ""
                )
                + " ".join(_person_stale(job, look))
            )
            # Description, owner attachments and recovery result commit together,
            # after provider calls have already kept their paid checkpoints.
            voice_changes.checkpoint(call, voice_changed=voice, text=result)
        return call.result


def _shown_before(session: Session, file: str) -> bool:
    """Whether the chat already shows `file`, as after a restart."""
    return Attachment.objects.filter(message__session=session, file=file).exists()


def _person_stale(job: Job, new_look: bool) -> list[str]:
    """What a new person made stale, scene by scene, for remake_person's answer: each scene's
    stale steps, and the scenes whose picture of the presenter in their place shows the
    person as they were."""
    said = [line for scene in job.scenes.order_by("number") if (line := stale.after_edit(scene))]
    faces = [
        scene.number
        for scene in job.scenes.order_by("number")
        if scene.shows and scene.person_shown == Scene.PersonShown.HAS_FACE
    ]
    if new_look and faces:
        said.append(
            f"The picture of the presenter in the place of {_scenes(faces)} shows the person "
            "as they were: it is drawn again for the new person when the scene's clip is next "
            "made."
        )
    said.append(
        "Make again what you decide the change needs with the scene tools, then assemble the ad."
        if len(said) > 0
        else "Nothing made for the ad is stale."
    )
    return said


class SetPlace(Tool):
    """Give a scene that shows the product a place of its own, for that scene only: a new
    place drawn fresh from your words (new_place), or a small edit of the picture of the place
    it has now (edit), which is cheaper and keeps the look already seen. A scene showing the
    presenter's face is drawn with the presenter standing in it; one showing nobody, the room
    alone. That picture is what its clips are sent of where it happens. Hands back the
    picture made and which steps it made stale: nothing is filmed until you make the scene's
    clip again. Look at it (look, what "place") before you do."""

    name = "set_place"

    scene: int = Field(description="The number of the scene.")
    new_place: str | None = Field(
        default=None,
        description="The new place, described in full, to be drawn fresh. Null for an edit.",
    )
    edit: str | None = Field(
        default=None,
        description="A small change to the picture of the scene's place as it is now, such as "
        "the light or what to clear away. Null for a new place.",
    )

    def run(self, call: ToolCall) -> str:
        scene = _a_planned_scene(call, self.scene)
        words = [each for each in (self.new_place, self.edit) if each is not None]
        if len(words) != 1:
            raise Refused("give a new place or an edit, one of the two.")
        if not words[0].strip():
            raise Refused("the words are blank: write the new place or the edit.")
        drawn = drawn_for(person_shown_for(scene.kind))
        try:
            made = (
                new_scene_place(scene, drawn, words[0])
                if self.new_place is not None
                else [edit_scene_place(scene, drawn, words[0])]
            )
        except NoPlaceYet as missing:
            raise Refused(
                f"the ad has no {PLACE_NAMES[drawn]} yet to edit: it is drawn with the first "
                "scene that shows the product. Give a new place instead."
            ) from missing
        except NoPresenterYet as missing:
            raise Refused(
                "the person hasn't been made yet, and the scene shows them: create the person "
                "first."
            ) from missing
        picture = made[-1]
        show_once(scene.job, made)
        stale_line = stale.after_edit(scene)
        return " ".join(
            [
                f"Made scene {scene.number}'s own {PLACE_NAMES[drawn]} (version "
                f"{picture.version}), for this scene only: the other scenes keep theirs, and "
                "it was shown to the shop owner in the chat. Its next clip is sent it. It cost "
                f"{dollars(call.cost_usd())}. Look at it (look, scene {scene.number}, what "
                '"place") before its clip is made.',
                stale_line or "Nothing made for the scene is stale.",
            ]
        )


class RunPlanningChecks(Tool):
    """Check every scene against the page and its clip limit, then shorten toward the
    target length. Remaining fact or line-length failures are yours to fix: rewrite,
    split or remove scenes with the scene tools and run these checks again. Never ask the
    shop owner to referee a failed check or supply a shorter line. If the ad still exceeds
    its target after shortening, it continues longer and code tells the owner its length."""

    name = "run_planning_checks"

    def run(self, call: ToolCall) -> str:
        job = _the_job(call)
        if job is None or not job.scenes.exists():
            raise Refused("the ad hasn't been planned yet, and the checks are run on its script.")
        try:
            problem = run_checks(job)
        except LineTooShort as short:
            post_notice(job, notices.PLANNING_CHECK_HANDED_BACK, str(short), Message.Level.PROBLEM)
            raise Refused(str(short)) from short
        if problem is None:
            return "\n".join(
                [
                    f"The checks passed. {why_the_checks_passed(job)} The ad is ready to render.",
                    _script(job),
                ]
            )
        fix = {
            "line": "Rewrite or remove the claim with the scene tools, using what the page "
            "states; then run the planning checks again. Do not ask the shop owner.",
            "line_length": "Split the scene or rewrite its line with the scene tools, then run "
            "the planning checks again. The clip limit applies to every scene, including "
            "talking scenes. Do not ask the shop owner.",
        }[problem.about]
        text = f"{problem.detail} Why: {problem.reason} {fix}"
        post_notice(
            job,
            notices.PLANNING_CHECK_HANDED_BACK,
            text,
            Message.Level.PROBLEM,
            scene=problem.scene.number if problem.scene else None,
        )
        return "\n".join([text, _script(job)])


class CreateMusic(Tool):
    """Create the ad's background music, in the mood you give, as long as the person's voice
    takes to say the script with a few seconds to spare. It is always instrumental. It isn't
    shown to the shop owner: they hear it in the finished ad. Only once the planning checks
    have passed."""

    name = "create_music"

    mood: str = Field(
        min_length=1,
        description='What the music should feel like, in a few words, such as "light upbeat '
        'lo-fi". Only the mood: it is always instrumental.',
    )

    def run(self, call: ToolCall) -> str:
        job = _the_job(call)
        if job is None or not job.scenes.exists():
            raise Refused("the ad hasn't been planned yet, and the music is as long as its script.")
        not_passed = why_the_checks_havent_passed(job)
        if not_passed is not None:
            raise Refused(not_passed)
        mood = music_mood(self.mood)
        prompt, seconds = music_prompt(mood), music_seconds(job)
        made = job.produced.filter(kind=ProducedItem.Kind.MUSIC, text=prompt, seconds=seconds)
        music = made.order_by("version").last()
        cost = dollars(_spent_on_music(job, prompt, seconds))
        if music is not None and music == latest(job, ProducedItem.Kind.MUSIC):
            return (
                "The music was already made in this mood for this script (version "
                f"{music.version}, {seconds} seconds), so nothing was made or paid for again. "
                f"Making it cost {cost}."
            )
        if music is not None:
            again = use_music_again(job, music)
            return (
                "The music was already made in this mood for this script (version "
                f"{music.version}), so it is the ad's music again as version {again.version}, "
                f"and nothing was made or paid for again. Making it cost {cost}."
            )
        music = create_music(job, prompt, seconds)
        return (
            f"Made the music (version {music.version}, {seconds} seconds): {mood}, "
            "instrumental. It isn't shown to the shop owner: they hear it in the finished ad."
        )


# B-roll pictures belong to the scene agent, including the first make.
NO_STARTING_PICTURE = "scene {scene}'s agent draws its starting picture; call start_scene_agent."


def _refuse_legacy_scene_tool(scene: Scene) -> None:
    """One switch policy for the temporary producer-owned scene tools."""
    raise Refused(
        "Every new clip is made by its scene agent. Call start_scene_agent, "
        "or pass_note for a change."
    )


class MakePicture(Tool):
    """Make a picture with the picture model, from your own prompt and pictures from the
    ad's library as references, and store it in the library as a new item with your label,
    to send like any other. Combining several sides of the product into one picture is one
    use of it. The picture is checked once: the product must be unchanged from its
    references and the picture show what your label says. Hands back the new item's number,
    or why it couldn't be made."""

    name = "make_picture"

    prompt: str = Field(
        description="What to draw, in full: the picture model reads only this and the "
        "references, as pictures 1, 2, 3... in the order given."
    )
    references: list[int] = Field(
        description="The library numbers of the pictures to make it from, in order. Empty "
        "to draw from the prompt alone."
    )
    label: str = Field(
        description="One sentence saying what the picture shows, as the library's labels "
        "do. It becomes the new item's label."
    )

    def run(self, call: ToolCall) -> str:
        job = _with_its_page(call, "a picture is made from its library")
        if not self.prompt.strip() or not self.label.strip():
            raise Refused("a picture needs a prompt and a label.")
        library = {item.number: item for item in library_of(job)}
        missing = [number for number in self.references if number not in library]
        if missing:
            held = (
                f"Its items are {listed_numbers(sorted(library))}." if library else "It is empty."
            )
            raise Refused(f"the ad's library has no item {listed_numbers(missing)}. {held}")
        try:
            made = make_picture(
                job,
                prompt=self.prompt,
                references=[library[number] for number in self.references],
                label=self.label.strip(),
                paid_under=call.pk,
            )
        except PictureNotMade as not_made:
            notices.post_notice(
                job,
                notices.MADE_PICTURE_FAILED,
                f"The producer's make_picture made no picture ({not_made}). The producer was "
                "told and carries on.",
                Message.Level.PROBLEM,
            )
            raise Failed(
                f"the picture wasn't stored, as {not_made}. Change the prompt or the "
                "references and make it again, or go on without it."
            ) from not_made
        return (
            f"Made library item {made.number}, checked once and passed: "
            f"{library_line(made)} Look at it (look, library_item {made.number}) before a "
            "scene's references name it."
        )


class OwnerPhotoShows(BaseModel):
    """One of the shop owner's photos to add to the library, and what it shows."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    photo: int = Field(
        description='The photo\'s number, as the chat gives it: "owner photo 3" is 3.'
    )
    shows: str = Field(
        description="The one thing in it to keep, in a few words, from what the shop owner "
        "said of it: the photo goes in as a clean picture of only that thing, drawn from it."
    )


class AddToLibrary(Tool):
    """Add photos and videos the shop owner attached in the chat, once the ad is planned, to
    the ad's library, so a scene's references can name them like any other item: each photo
    as a clean picture of only the thing its `shows` names, drawn from it (never the photo as
    sent), and labelled; each video watched into moments, its look frames added. Hands back
    each new item's number and label, and each video's moments. Before the plan, their
    product photos go in with use_photos."""

    name = "add_to_library"

    photos: list[OwnerPhotoShows] = Field(
        description="The shop owner's photos to add, each once. Empty for none."
    )
    videos: list[int] = Field(
        description="The shop owner's videos to add, by their number as the chat gives it: "
        '"owner video 2" is 2. Empty for none.'
    )

    def run(self, call: ToolCall) -> str:
        job = _with_its_page(call, "their files go into its library")
        if not job.scenes.exists():
            raise Refused(
                "the ad isn't planned yet: before the plan, the shop owner's product photos go "
                "in with use_photos."
            )
        photos, videos = messages.owner_photos(call.session), messages.owner_videos(call.session)
        numbers = [each.photo for each in self.photos]
        if not numbers and not self.videos:
            raise Refused("name at least one of the shop owner's photos or videos.")
        if len(set(numbers)) != len(numbers) or len(set(self.videos)) != len(self.videos):
            raise Refused("name each photo and video once.")
        wrong = [n for n in numbers if not 1 <= n <= len(photos)]
        wrong_videos = [n for n in self.videos if not 1 <= n <= len(videos)]
        if wrong or wrong_videos:
            raise Refused(
                f"the chat has {_owner_photos(len(photos))} and {_owner_videos(len(videos))}."
            )
        if any(not each.shows.strip() for each in self.photos):
            raise Refused("say what each photo shows.")
        taken: list[tuple[str, owner_answers.Taken]] = [
            (
                f"Owner photo {each.photo}: ",
                owner_answers.into_the_library(
                    job,
                    photos=[photos[each.photo - 1].file],
                    videos=[],
                    shows=each.shows.strip(),
                    cleaned_of=each.shows.strip(),
                    without_it="send a clearer photo of it",
                ),
            )
            for each in self.photos
        ]
        for number in self.videos:
            taken.append(
                (
                    f"Owner video {number}: ",
                    owner_answers.into_the_library(
                        job,
                        photos=[],
                        videos=[videos[number - 1].file],
                        shows="",
                        cleaned_of="",
                        without_it="",
                    ),
                )
            )
        added = [item for _, each in taken for item in each.added]
        watched = [video for _, each in taken for video in each.watched]
        with transaction.atomic():
            owner_answers.say_unreadable(job, sum(each.unreadable for _, each in taken))
            # A photo in is one whose clean picture was kept; a video's look frames join the
            # library too, but are the video's, not photos of their own.
            photos_in = sum(1 for _, each in taken[: len(self.photos)] if each.added)
            if photos_in or watched:
                post_notice(
                    job, notices.REPLY, _what_went_in(photos_in, len(watched)), Message.Level.INFO
                )
        said = "\n".join(
            named + line[:1].lower() + line[1:] for named, each in taken for line in each.said
        )
        if not added and not watched:
            raise Failed(f"nothing was added to the library.\n{said}")
        return (
            f"{said}\nFor B-roll, pass the scene its note with pass_note; its agent picks photos "
            "from the library. Look at one first (look, library_item N) when you need to "
            "know what it shows; ask_owner can ask what to take from an owner photo, naming "
            "it in the question's photos."
        )


def _what_went_in(photos_in: int, videos_in: int) -> str:
    """The shop owner's reply for what they sent that went in: how many photos and videos."""
    photos = f"{photos_in} photo{'s' if photos_in != 1 else ''}" if photos_in else ""
    videos = f"{videos_in} video{'s' if videos_in != 1 else ''}" if videos_in else ""
    return f"Thanks, I have your {' and '.join(each for each in (photos, videos) if each)}."


def _owner_videos(count: int) -> str:
    return (
        "no owner video"
        if count == 0
        else f"owner videos 1 to {count}"
        if count > 1
        else "owner video 1"
    )


def _clip_with_the_photos_in_use(
    scene: Scene, clips: QuerySet[ProducedItem]
) -> ProducedItem | None:
    """The newest of `clips` made with the shop owner's photos the scene uses now, if any."""
    using = {(row.file, row.thing.casefold()) for row in _photos_in_use(scene)}
    return next(
        (
            clip
            for clip in reversed(clips)
            if clip.step is not None and _photos_of(clip.step) == using
        ),
        None,
    )


def _no_clip_being_made(scene: Scene, then: str = "make its starting picture again then") -> None:
    """Refuse what changes a clip's inputs while the scene's clip is being made, such as a new
    starting picture or new references: that clip, made from what came before, would finish
    the scene once the change had un-finished it (#133). `then` says what to do once it's
    ready."""
    if scene.steps.filter(
        kind__in=[SceneStep.Kind.CLIP, SceneStep.Kind.SCENE_AGENT], status=SceneStep.Status.RUNNING
    ).exists():
        raise Refused(
            f"scene {scene.number}'s clip is still being made. You'll be told when it's "
            f"ready; {then}."
        )


def _heard_otherwise(audio: ProducedItem, line: str) -> str | None:
    """What `audio` was heard saying when its transcript's words aren't `line`'s, compared
    as letters and digits only, so spacing, case and punctuation don't count; None when they
    are, or when it hasn't been transcribed."""
    heard = (
        ProducedItem.objects.filter(
            made_from=audio,
            kind=ProducedItem.Kind.TRANSCRIPT,
            step__status=SceneStep.Status.FINISHED,
        )
        .order_by("version")
        .last()
    )
    if heard is None or _letters_and_digits(heard.text) == _letters_and_digits(line):
        return None
    return heard.text


def _letters_and_digits(words: str) -> str:
    return "".join(character for character in words.lower() if character.isalnum())


def _made_for_the_plan(scene: Scene) -> dict[str, Any]:
    """The filter for clips whose step was made for the scene as it stands, its line aside:
    the audio a clip speaks holds its line."""
    made_for = SceneStep.made_for(scene, through="step__")
    del made_for["step__line"]
    return made_for


def _sent_files_since(call: ToolCall, question: OwnerQuestion) -> bool:
    """Whether the shop owner attached a photo or a video since the fixing agent's question:
    only that answers it. Words alone change nothing, so a later note is fixed as any is."""
    return Attachment.objects.filter(
        message__session=call.session,
        message__role=Message.Role.USER,
        message__created_at__gt=question.asked_at,
        message__created_at__lte=call.created_at,
        kind__in=[Attachment.Kind.PICTURE, Attachment.Kind.VIDEO],
    ).exists()


def _hand_the_answer_back(
    call: ToolCall,
    scene: Scene,
    question: OwnerQuestion,
    instruction: str,
    checked_quotes: list[dict[str, Any]] | None = None,
) -> str:
    """Hand the shop owner's answer to the fixing agent's question to the scene's fix (spec
    #247 point 11, as the round 7 spec's section 4 changed it): every message they wrote since
    it was asked, word for word, with the photos and videos attached, and the producer's
    `instruction`. Only a photo or a video reaches the fix; words alone change nothing."""
    written = list(
        call.session.messages.filter(
            role=Message.Role.USER,
            created_at__gt=question.asked_at,
            created_at__lte=call.created_at,
        )
        .prefetch_related("attachments")
        .order_by("seq")
    )
    words = "\n".join(each.text.strip() for each in written if each.text.strip())
    files = [attached for each in written for attached in each.attachments.all()]
    try:
        handed = owner_answers.answer(
            question,
            words=words,
            photos=[each.file for each in files if each.kind == Attachment.Kind.PICTURE],
            videos=[each.file for each in files if each.kind == Attachment.Kind.VIDEO],
            instruction=instruction,
            checked_quotes=checked_quotes,
            handoff_call=call,
        )
    except ValueError:
        raise Refused(
            f"the shop owner hasn't answered the scene's question about scene "
            f'{scene.number} yet: "{question.question}" Call pass_note once they answer.'
        ) from None
    except owner_answers.NotWaiting as not_waiting:
        raise Refused(f"the question takes no answer now: {not_waiting}") from None
    number = scene.number
    return f"Handed the owner's answer to scene {number}'s agent: " + (
        "it starts a new run in the same chat."
        if handed.kind == "new_fix"
        else "the answer reaches its next turn while it keeps working."
        if handed.kind == "running"
        else "the question is closed; no media was sent."
    )


class _SceneFields(Tool):
    """What a plan edit can give a scene, each null to leave it as it is."""

    kind: Literal["talking", "broll"] | None = Field(
        default=None,
        description="Talking on camera, or hands and product with her voice off camera. "
        "Null keeps it.",
    )
    kind_why: str | None = Field(
        default=None,
        description="For B-roll, why her hands cannot show the line at arm's length. "
        "Null keeps it.",
    )
    line: str | None = Field(
        default=None,
        description="The scene's new line, exactly what the person says. Null keeps it.",
    )
    shows: str | None = Field(
        default=None,
        description="The scene's one product action while its line is said. "
        "Both kinds need an action. Null keeps it.",
    )
    broll_kind: BrollKind | None = Field(
        default=None,
        description='For a scene that shows the product: "does a job", "shows the problem" '
        'or "showcase". Null keeps it.',
    )
    person_shown: PersonShown | None = Field(
        default=None,
        description='For a scene that shows the product: "has face" when the presenter\'s '
        'face is in it, "no face" when it isn\'t. Null keeps it.',
    )
    usage: str | None = Field(
        default=None,
        description="For a scene that shows the product: how the product is used in it, from "
        "the page; an empty string when it isn't used. Null keeps it.",
    )
    result: str | None = Field(
        default=None,
        description='For a "does a job" scene: the result you can see at its end; an empty '
        "string for the other kinds. Null keeps it.",
    )
    part: Part | None = Field(
        default=None, description="The part of the script the scene plays. Null keeps it."
    )
    overlay: str | None = Field(
        default=None,
        description="Code draws these words on the finished ad, along the top; an empty string "
        "for none. Null keeps it.",
    )
    talking_style: TalkingStyle | None = Field(
        default=None,
        description="For a talking scene, how the presenter is filmed. Null keeps it.",
    )
    product_state: ProductState | None = Field(
        default=None,
        description="For a talking scene, where the product is. Null keeps it.",
    )
    shows_to_lens: bool | None = Field(
        default=None,
        description="Whether the scene's action only shows the product to the lens. Null keeps it.",
    )
    packaging_photo: StrictInt | None = Field(
        default=None,
        description="The library number of the photo that shows the packaging, for a box "
        "state. Null keeps it.",
    )

    @field_validator("packaging_photo")
    @classmethod
    def _a_library_number(cls, number: int | None) -> int | None:
        return packaging_photo_number(number)

    def _fields(self) -> dict[str, Any]:
        return {name: getattr(self, name) for name in plan_edits.EDITABLE}


class EditScene(_SceneFields):
    """Change any of a scene's fields in the plan: its line, whether it shows the product or
    the person talking and what it shows, its labels, its part, its overlay. Hands back which
    of the scene's steps the change made stale; nothing is made again until you call the
    scene tools for it. A changed line, or a change to what the scene shows, is checked
    against the page again: run the planning checks before making anything for it."""

    name = "edit_scene"

    scene: int = Field(description="The number of the scene.")

    def run(self, call: ToolCall) -> str:
        scene = _a_planned_scene(call, self.scene)
        try:
            edited = plan_edits.edit_scene(scene, self._fields())
        except plan_edits.CantEdit as cant:
            raise Refused(str(cant)) from None
        if not edited.changed:
            return f"Scene {scene.number} already is as asked, so nothing was changed."
        return _plan_edited(scene.job, edited)


class AddScene(_SceneFields):
    """Add a scene to the plan at the number you give, the scenes from there on moved one
    later. It has nothing made yet: run the planning checks on it, then make its steps."""

    name = "add_scene"

    at: int = Field(description="The new scene's number: 1 to one past the last scene.")

    def run(self, call: ToolCall) -> str:
        job = _a_planned_job(call)
        if not self.line:
            raise Refused("a new scene needs its line: what the person says in it.")
        try:
            edited = plan_edits.add_scene(job, self.at, self._fields())
        except plan_edits.CantEdit as cant:
            raise Refused(str(cant)) from None
        return _plan_edited(job, edited)


class RemoveScene(Tool):
    """Take a scene out of the plan, the scenes after it moved one earlier. What was made and
    paid for it is kept, out of the ad."""

    name = "remove_scene"

    scene: int = Field(description="The number of the scene.")

    def run(self, call: ToolCall) -> str:
        scene = _a_planned_scene(call, self.scene)
        try:
            edited = plan_edits.remove_scene(scene)
        except plan_edits.CantEdit as cant:
            raise Refused(str(cant)) from None
        return _plan_edited(scene.job, edited)


class ReorderScenes(Tool):
    """Play the scenes in a new order. Nothing made for them goes stale."""

    name = "reorder_scenes"

    order: list[int] = Field(
        description="Every scene's number as it is now, once each, in the order they are to "
        "play, such as [2, 1, 3]."
    )

    def run(self, call: ToolCall) -> str:
        job = _a_planned_job(call)
        try:
            edited = plan_edits.reorder(job, self.order)
        except plan_edits.CantEdit as cant:
            raise Refused(str(cant)) from None
        return _plan_edited(job, edited)


def _a_planned_job(call: ToolCall) -> Job:
    job = _the_job(call)
    if job is None or not job.scenes.exists():
        raise Refused("the ad hasn't been planned yet, so there is no plan to change.")
    return job


def _a_planned_scene(call: ToolCall, number: int) -> Scene:
    job = _a_planned_job(call)
    scene = job.scenes.filter(number=number).first()
    if scene is None:
        raise Refused(f"the ad has no scene {number}: its scenes are 1 to {job.scenes.count()}.")
    return scene


def _plan_edited(job: Job, edited: plan_edits.Edited) -> str:
    """What the producer is told of a plan edit: what changed, what it made stale, what is to
    be checked, and the plan as it stands."""
    said = [
        "Changed the plan: "
        + "; ".join(
            f"scene {number} {_listed(fields)}"
            if fields[0] in ("added", "removed", "moved")
            else f"scene {number}'s {_listed(fields)}"
            for number, fields in edited.changed.items()
        )
        + "."
    ]
    said += edited.also
    said += edited.stale or ["Nothing made for the ad is stale."]
    if edited.to_check:
        said.append(
            f"The planning checks haven't checked {_scenes(edited.to_check)} against the page: "
            "run them before making anything for it."
        )
    said.append(
        "Make again what you decide the change needs with the scene tools, then assemble the ad."
    )
    return "\n".join([" ".join(said), _script(job), _overlays(job)])


class SetReferences(Tool):
    """Keep the owner's library-photo exclusions for a scene that shows the product.
    The scene agent chooses its photos: leave pictures null. Keep owner exclusions in
    exclude, choose a watched page-video moment in piece, or remove the moment with
    no_piece. Changes make dependent work stale; pass_note gives the agent the owner's
    checked direction and it looks at the moment before filming."""

    name = "set_references"

    scene: int = Field(description="The number of the scene.")
    pictures: list[ScenePicture] | None = Field(
        default=None,
        description="Leave null: the scene agent chooses its photos.",
    )
    piece: ScenePiece | None = Field(
        default=None,
        description="The watched page-video moment for the scene; null keeps its current moment.",
    )
    no_piece: bool = Field(
        default=False, description="True removes the video moment; otherwise piece can replace it."
    )
    exclude: list[int] | None = Field(
        default=None,
        description="The library numbers never sent for this scene, the whole list: it "
        "replaces the scene's. Empty to exclude nothing; null keeps the scene's.",
    )

    def run(self, call: ToolCall) -> str:
        scene = _a_checked_scene(call, self.scene)
        if not scene.shows:
            raise Refused(
                f"scene {scene.number} is said to camera: its clip is sent no pictures from "
                "the library."
            )
        if self.pictures is not None:
            raise Refused(
                "the scene agent picks its photos; pass_note gives it the owner's direction. "
                "set_references keeps owner exclusions and the scene's video moment."
            )
        _no_clip_being_made(scene, then="set its exclusions then")
        pictures = (
            [ScenePicture.model_validate(picture) for picture in scene.scene_pictures]
            if self.pictures is None
            else self.pictures
        )
        piece = (
            None
            if self.no_piece
            else self.piece
            if self.piece is not None
            else None
            if scene.piece is None
            else ScenePiece.model_validate(scene.piece)
        )
        exclude = list(scene.excluded) if self.exclude is None else self.exclude
        _refuse_unknown_references(scene, [], piece, exclude)
        hard_list.refuse_part_picture_elsewhere(scene, [picture.number for picture in pictures])
        wanted = {
            "scene_pictures": [picture.model_dump(by_alias=True) for picture in pictures],
            "piece": None if piece is None else piece.model_dump(),
            # A set: the same items in another order are the same list.
            "excluded": sorted(set(exclude)),
        }
        changed = [field for field, value in wanted.items() if getattr(scene, field) != value]
        if changed:
            Scene.objects.filter(pk=scene.pk).update(**wanted)
            for field, value in wanted.items():
                setattr(scene, field, value)
        excluded_words = listed_numbers(sorted(set(exclude))) if exclude else "none"
        told = [
            f"Scene {scene.number}'s owner exclusions are {excluded_words}; "
            "its scene agent chooses every other photo. "
            + (
                f"Video moment: video {piece.video}, moment {piece.moment}."
                if piece
                else "No video moment."
            )
        ]
        if changed:
            # A clip made for the earlier references is stale now (spec #247 point 9).
            told += filter(None, [stale.after_edit(scene)])
        else:
            told.append("Nothing changed: the scene had these references already.")
        told.append(
            "The video model receives only the scene's starting picture"
            + (" as Image 1 plus the chosen video piece." if piece else ".")
            + " The scene agent chooses photos for drawing that picture; those photos "
            "are not sent to the video model."
        )
        return " ".join(told)


def _refuse_unknown_references(
    scene: Scene, pictures: list[ScenePicture], piece: ScenePiece | None, exclude: list[int]
) -> None:
    """Refuse references the ad doesn't have, or that contradict each other, before anything
    is kept: the planner's own checks of a plan's pictures and moment."""
    job = scene.job
    numbers = [item.number for item in library_of(job)]
    missing = pictures_missing([*(picture.number for picture in pictures), *exclude], numbers) or (
        piece_missing(scene.number, piece, moments_by_video(job))
    )
    if missing is not None:
        raise Refused(missing)
    named = [picture.number for picture in pictures]
    twice = sorted({number for number in named if named.count(number) > 1})
    if twice:
        raise Refused(f"library item {listed_numbers(twice)} is named twice: name each once.")
    originals = {scene_references.original_number(job, number) for number in exclude}
    both = [n for n in named if scene_references.original_number(job, n) in originals]
    if both:
        raise Refused(
            f"library item {listed_numbers(both)} is both a scene picture and excluded "
            "(or a version of one that is): a picture is sent or excluded, not both."
        )


def _references_words(scene: Scene) -> str:
    """The scene's references as they now stand."""
    pictures = ", ".join(
        f"{picture['number']} ({picture['for']})" for picture in scene.scene_pictures
    )
    piece = scene.piece
    return (
        f"Scene {scene.number}'s references: pictures {pictures or 'none'}; video piece "
        + ("none" if piece is None else f"video {piece['video']}, moment {piece['moment']}")
        + "; excluded "
        + (listed_numbers(list(scene.excluded)) if scene.excluded else "nothing")
        + "."
    )


class ApproveOwnerPart(Tool):
    """Keep the shop owner's yes to the picture of a part of the product a scene shows that
    no photo shows, so the clip of every scene showing it can be made. Call it only when they
    said yes to that picture in their own words: never on silence, a question or a change
    they asked for, and never decide for them."""

    name = "approve_owner_part"
    # Keeps a yes; draws nothing.
    runs_while_paused = True

    scene: int = Field(description="The number of the scene.")

    def run(self, call: ToolCall) -> str:
        scene = _a_checked_scene(call, self.scene)
        part = owner_parts.owner_part(scene)
        if part is None:
            raise Refused(
                f"scene {scene.number} shows no part the shop owner has to say yes to, so its "
                "clip is made as usual."
            )
        if not owner_parts.picture_is_current(scene):
            raise Refused(
                f"scene {scene.number} has no picture of {part.what} as it is now. Make its "
                "clip, which draws the picture and shows it to the shop owner, and ask them "
                "once it is shown."
            )
        if owner_parts.is_approved(scene):
            return (
                f"The shop owner already said yes to the picture of {part.what} "
                f"({_library_picture(scene)}). Make scene {scene.number}'s clip."
            )
        drawn = scene.owner_part_drawn
        assert drawn is not None, "a current picture was drawn"
        # The yes is the shop owner's latest message since the picture was shown and before
        # the call was made: the one this call answers, never one its turn didn't read.
        said = (
            call.session.messages.filter(
                role=Message.Role.USER,
                created_at__gt=datetime.fromisoformat(str(drawn["shown_at"])),
                created_at__lt=call.created_at,
            )
            .order_by("seq")
            .last()
        )
        if said is None:
            raise Refused(
                f"the shop owner hasn't answered since the picture of {part.what} "
                f"({_library_picture(scene)}) was shown to them. Ask them, and wait for their "
                "answer."
            )
        owner_parts.approve(scene, said)
        return (
            f"Kept the shop owner's yes to the picture of {part.what} "
            f"({_library_picture(scene)}). Make scene {scene.number}'s clip."
        )


class CorrectOwnerPart(Tool):
    """Draw the picture of a part of the product a scene shows that no photo shows again,
    once the shop owner said what to change in it, or sent a photo of that part. The part
    is one thing for the whole ad: every scene showing it without a clip yet takes the
    change. The new picture is shown to them for their yes."""

    name = "correct_owner_part"

    scene: int = Field(description="The number of the scene.")
    words: str | None = Field(
        description="How the part should look now, as one sentence in the shop owner's own "
        "words. When they only said what to change, the part's look as it was, with their "
        "change in it. Null when they only sent a photo."
    )
    photo: int | None = Field(
        description="The photo of the part they attached, by its number as the chat gives it: "
        '"owner photo 3" is 3. Null for none.'
    )

    def run(self, call: ToolCall) -> str:
        scene = _a_checked_scene(call, self.scene)
        part = owner_parts.owner_part(scene)
        if part is None:
            raise Refused(
                f"scene {scene.number} shows no part drawn without a photo, so there is nothing "
                "of it to correct. Make its clip again with a note instead."
            )
        words = normalised_note(self.words)
        if not words and self.photo is None:
            raise Refused(
                "say how the shop owner wants the part to look, in their words, or which photo "
                "of it they sent."
            )
        file = None
        if self.photo is not None:
            attached = messages.owner_photos(call.session)
            if not 1 <= self.photo <= len(attached):
                raise Refused(f"the chat has {_owner_photos(len(attached))}.")
            file = attached[self.photo - 1].file
        _no_clip_being_made(scene)
        try:
            if file is not None:
                owner_parts.photo_correction(scene, file, words)
            else:
                owner_parts.words_correction(scene, words)
        except ReferencePhotoUnusable as unusable:
            raise Refused(str(unusable)) from unusable
        scene.refresh_from_db()
        call.asked_about = "the owner part"
        return _part_drawn(scene, part, "The clip of every scene showing it isn't made")


class AskOwner(Tool):
    """Ask the shop owner one short, specific question in the chat, and wait for their
    answer: your turn ends once its other calls are done, and their next message answers
    it. Start everything else that can go on in the same turn, and don't repeat the question
    in your own words. Ask only when the ad
    can't go on without them: their yes to the picture of a part of the product no photo
    shows, something the ad needs that neither the page nor the library holds, or what a
    tool handed back for them to answer. Never ask what you can answer from what you
    already have."""

    name = "ask_owner"
    ends_the_turn = True
    runs_while_paused = True

    question: str = Field(
        min_length=1,
        description="The one question, as the shop owner reads it in the chat: plain words, "
        "for someone who isn't technical.",
    )
    photos: list[int] = Field(
        default=[],
        description="The shop owner's photos the question is about, by their number as the "
        'chat gives it: "owner photo 3" is 3. Empty for none.',
    )

    def run(self, call: ToolCall) -> str:
        question = self.question.strip()
        if not question:
            raise Refused("the question is blank: say the one thing to ask the shop owner.")
        # A call run again after a restart has asked already.
        # The shop owner reads the total in it, never its stand-in.
        question = planning_questions.question_to_show(call, question)
        question = loop.with_the_total(question, call.session)
        shown = (
            planning_questions.already_shown(call)
            or call.session.messages.filter(
                role=Message.Role.AGENT, text=question, created_at__gte=call.created_at
            ).first()
        )
        if shown is None:
            shown = messages.add(call.session, role=Message.Role.AGENT, text=question)
        planning_questions.question_shown(call, shown)
        return (
            "Asked the shop owner in the chat. Your turn ends here: their next message answers "
            "the question."
        )


def _part_drawn(scene: Scene, part: owner_parts.OwnerPart, waits: str) -> str:
    """Draw the picture of the scene's owner part as it stands, shown to the shop owner, and
    say what the producer does next: ask them about it. `waits` starts the sentence saying
    which clips wait."""
    try:
        owner_parts.draw_part_picture(scene)
    except owner_parts.PartPictureFailed as failed:
        then = (
            "It is drawn again when its clip is next asked for."
            if failed.retried
            else "Ask them how it looks or for a photo of it, and correct it with "
            "correct_owner_part."
        )
        return (
            f"Scene {scene.number} shows {part.what}, which no photo shows, and making its "
            f"picture failed: {failed}. The chat told the shop owner. {waits} until they say "
            f"yes to a picture of it. {then}"
        )
    return (
        f"Scene {scene.number} shows {part.what}, which no photo shows: its picture was drawn "
        f"({_library_picture(scene)}) and shown to the shop owner in the chat. {waits} until "
        f"they say yes to it. Ask them, in plain words, to look at {part.what} and say yes if "
        "it looks right, or say what to change, or send a photo of it, and wait for their "
        "answer."
    )


def _library_picture(scene: Scene) -> str:
    """The scene's owner part's picture, by its library number, such as "library picture 7"."""
    scene.refresh_from_db()
    item = owner_parts.part_picture(scene)
    assert item is not None, "the part has its picture"
    return f"library picture {item.number}"


class AssembleAd(Tool):
    """Assemble the finished ad from every scene's clip, in order, with captions of the
    written line timed from its transcript, the music under the voice and each scene's
    overlay, and show it to the shop owner in the chat. Only once every scene is
    finished and the music is made. Costs nothing."""

    name = "assemble_ad"
    runs_while_paused = True

    def run(self, call: ToolCall) -> str:
        job = _the_job(call)
        if job is None or not job.scenes.exists():
            raise Refused("the ad hasn't been planned yet, and it is assembled from its scenes.")
        ad, again = assemble_the_ad(call.session, job)
        if again:
            return (
                f"The ad was already assembled from these clips, overlays, this music and "
                "end card "
                f"(version {ad.version}, {ad.seconds:g} seconds) and shown to the shop "
                "owner, so nothing was made again. Assembling costs nothing."
            )
        first, *rest = ad.cuts
        plays = [
            f"Scene {first['scene']} plays from {first['start']:g} to {first['end']:g} seconds"
        ]
        plays += [f"scene {cut['scene']} from {cut['start']:g} to {cut['end']:g}" for cut in rest]
        if ad.end_card is not None:
            plays.append(f"the end card from {ad.end_card['start']:g} to {ad.end_card['end']:g}")
        how = "each clip whole with its own sound, with captions of the written line, "
        return (
            f"Assembled the ad (version {ad.version}, {ad.seconds:g} seconds) from "
            f"{how}the music under the voice, each scene's overlay and the end card, and showed "
            f"it to the shop owner in the chat. {_listed(plays)}."
            f"{_spent_since_the_ad_before(call.session, ad)} Tell the shop owner."
        )


def _spent_since_the_ad_before(session: Session, ad: ProducedItem) -> str:
    """What every model call of the session cost since the ad before `ad` was assembled, the
    producer's own turns too, as a sentence to follow another (spec #247 point 10: after a
    change the shop owner is told in one line what it cost). Blank for the first ad."""
    before = (
        ProducedItem.objects.filter(
            job=ad.job, kind=ProducedItem.Kind.FINISHED_AD, version__lt=ad.version
        )
        .order_by("version")
        .last()
    )
    if before is None:
        return ""
    spent: Decimal | None = session.model_calls.filter(created_at__gt=before.created_at).aggregate(
        cost=Sum("cost_usd")
    )["cost"]
    return (
        f" The changes since ad version {before.version} cost {dollars(spent or Decimal(0))}:"
        " tell the shop owner in one line what changed and what it cost."
    )


class UseVersion(Tool):
    """Use one of a scene's versions again, numbered as the scene's card and read_ad number
    them (v1 is its first), for nothing: its clip is brought back as the scene's newest
    version, with nothing made, judged or paid for, and once an ad has been shown it is
    assembled again with it. Only a version whose clip still says the scene's line as it
    stands, in the person's voice: for another, make the scene's steps again instead."""

    name = "use_version"
    # Spends nothing.
    runs_while_paused = True

    scene: int = Field(description="The number of the scene.")
    version: int = Field(description="The number of the version to use: 1 for v1, and so on.")

    def run(self, call: ToolCall) -> str:
        scene = _a_planned_scene(call, self.scene)
        number = scene.number
        made = versions(scene)
        if not 1 <= self.version <= len(made):
            raise Refused(
                f"scene {number} has versions 1 to {len(made)}, not {self.version}."
                if made
                else f"scene {number} has no versions yet: none of its clips has been made."
            )
        chosen = made[self.version - 1]
        if not go_back_is_free(scene, self.version):
            raise Refused(
                f"scene {number}'s {chosen.label} has no clip that still says the scene's line "
                "as it stands, in the person's voice, so it can't be used for nothing. Make the "
                "scene's stale steps again instead."
            )
        in_use = _current_clip_in_use(scene)
        if in_use is not None and chosen.clip is not None and in_use.file == chosen.clip.file:
            return (
                f"Scene {number}'s clip in use is already the one of {chosen.label}, so nothing "
                "changed."
            )
        try:
            gone = go_back(scene, self.version)
        except SceneBusy as busy:
            raise Refused(
                f"{busy}. You'll be told when it's ready; use the version then."
            ) from busy
        # Refused above unless the version is free, so its clip came back with it.
        assert not gone.clip_to_make, "a free version brings its clip back"
        return (
            f"Scene {number} uses {chosen.label} again, as {gone.version.label}: its clip was "
            f"brought back, and nothing was made or paid for.{_assembled_again(call)}"
        )


class KeepTry(Tool):
    """Put one of the tries at a scene's clip in the ad instead of the one there, for nothing:
    its first clip or a try the fixing agent filmed after the clip judge failed it, as the chat
    showed the shop owner every try to pick from; or, as try 0, the clip a clip step made
    before it ended without finishing the scene. Once an ad has been shown it is assembled
    again with it."""

    name = "keep_try"
    # Spends nothing.
    runs_while_paused = True

    scene: int = Field(description="The number of the scene.")
    try_number: int = Field(description="Which try: 0 for the first clip, 1 for try 1, and so on.")

    def run(self, call: ToolCall) -> str:
        scene = _a_planned_scene(call, self.scene)
        number = scene.number
        unfinished = kept_before_it_failed(scene)
        if unfinished is not None and self.try_number == 0:
            try:
                keep_the_clip_before_it_failed(unfinished)
            except (SceneBusy, NoSuchVersion) as refused:
                raise Refused(f"{refused}.") from refused
            return (
                f"Scene {number}'s clip (version {unfinished.version}), made before its step "
                "ended, is the scene's clip now: nothing was made or paid for, and scene "
                f"{number} is finished.{_assembled_again(call)}"
            )
        step = (
            scene.steps.filter(kind=SceneStep.Kind.CLIP, tries_shown__isnull=False)
            .distinct()
            .order_by("id")
            .last()
        )
        if step is None:
            raise Refused(
                f"scene {number} has no tries to pick from: there are tries only of a clip the "
                "clip judge failed and the fixing agent filmed again. Use use_version for its "
                "versions."
            )
        shown = tries_shown(step)
        picked = next(
            (each.clip for each in shown.tries if (each.clip.remake or 0) == self.try_number),
            None,
        )
        if picked is None:
            names = _listed([each.name.lower() for each in shown.tries])
            raise Refused(f"scene {number}'s tries are its {names}, not {self.try_number}.")
        try:
            use_try(picked)
        except TryNotOnOffer as refused:
            raise Refused(str(refused)) from refused
        # A try swapped in moves the scene past any question its fixing agent asked about it.
        owner_answers.withdraw(scene)
        return (
            f"Scene {number}'s {try_name(picked)} is in the ad now, as "
            f"{versions(scene)[-1].label}: nothing was made or paid for."
            f"{_assembled_again(call)}"
        )


def _assembled_again(call: ToolCall) -> str:
    """Once an ad has been shown, assemble it again from the scenes as they stand and say so,
    as a sentence to follow another; or say why it wasn't. Blank before the first ad: the
    producer assembles that one once every scene is made."""
    return assembled_again(call.session, _the_job(call))


def assembled_again(session: Session, job: Job | None) -> str:
    """`_assembled_again` for the session's `job`, as code does after a version swapped in
    from a scene's card."""
    if job is None or not job.produced.filter(kind=ProducedItem.Kind.FINISHED_AD).exists():
        return ""
    try:
        ad, _ = assemble_the_ad(session, job)
    except Refused as held:
        return f" The ad wasn't assembled again yet: {held} Assemble it once it can be."
    except Failed as failed:
        return f" The ad couldn't be assembled again with it: {failed}"
    return (
        f" The ad was assembled again with it (version {ad.version}, {ad.seconds:g} seconds) "
        "and shown to the shop owner. Tell them in one line what changed: it cost nothing."
    )


class CostSoFar(Tool):
    """What the ad has cost so far, in all and by part: each scene's steps, with the fixing
    agent's work on them; each of your other tools; and your own turns. Costs nothing."""

    name = "cost_so_far"
    # Reads what is kept; spends nothing.
    runs_while_paused = True

    def run(self, call: ToolCall) -> str:
        paid = call.session.model_calls.all()
        total = _summed(paid)
        job = _the_job(call)
        scenes = list(job.scenes.order_by("number")) if job is not None else []
        by_scene = {
            scene.number: _summed(
                paid.filter(
                    Q(tool_call__scene_steps__scene=scene)
                    | Q(tool_call__agent_fix__step__scene=scene)
                ).distinct()
            )
            for scene in scenes
        }
        rest = (
            paid.exclude(purpose=PRODUCER.purpose)
            .filter(tool_call__agent=PRODUCER.name, tool_call__scene_steps__isnull=True)
            .values_list("tool_call__tool")
            .annotate(cost=Sum("cost_usd"))
            .order_by("tool_call__tool")
        )
        turns = _summed(paid.filter(purpose=PRODUCER.purpose))
        counted = sum(by_scene.values(), Decimal(0)) + turns
        counted += sum((cost or Decimal(0) for _, cost in rest), Decimal(0))
        parts = [
            *(f"scene {number} {dollars(cost)}" for number, cost in by_scene.items()),
            *(f"{tool} {dollars(cost or Decimal(0))}" for tool, cost in rest),
            f"your own turns {dollars(turns)}",
            # Anything charged to none of those, as the app's own model calls on messages, so
            # the parts add up to the total.
            *([f"the rest {dollars(total - counted)}"] if total != counted else []),
        ]
        return f"The ad has cost {dollars(total)} so far: {_listed(parts)}."


def _summed(calls: QuerySet[ModelCall]) -> Decimal:
    """What `calls` cost, together."""
    cost: Decimal | None = calls.aggregate(cost=Sum("cost_usd"))["cost"]
    return cost or Decimal(0)


class ReadAd(Tool):
    """Read a part of the ad in full, as it stands now: one scene, with its plan, each of its
    versions, what each clip was sent (every picture with its tier, every piece of a page
    video, what was left out, the recolours and the prompt) and what its work cost; or, with
    no scene, the whole plan and every scene so. The ad as it stands, given to you last on
    every turn, is the short form of it. Costs nothing."""

    name = "read_ad"
    # Reads what is kept; spends nothing.
    runs_while_paused = True

    scene: int | None = Field(description="The number of the scene to read, or null for all.")

    def run(self, call: ToolCall) -> str:
        job = _the_job(call)
        if job is None or not job.scenes.exists():
            raise Refused("the ad hasn't been planned yet, so there is nothing of it to read.")
        if self.scene is not None:
            scene = job.scenes.filter(number=self.scene).first()
            if scene is None:
                raise Refused(
                    f"the ad has no scene {self.scene}: its scenes are 1 to {job.scenes.count()}."
                )
            return snapshot.scene_in_full(scene)
        planned = job.model_calls.filter(
            purpose="plan_ad", outcome=ModelCall.Outcome.SUCCEEDED, decision="plan"
        ).last()
        return snapshot.ad_in_full(job, _the_plan(job, planned.reason if planned else ""))


class ReadEarlierCall(Tool):
    """Read the whole answer of one of your earlier tool calls, such as one folded to one
    line to save room in your conversation, by its call id. Costs nothing."""

    name = "read_earlier_call"
    # Reads what is kept; spends nothing.
    runs_while_paused = True

    call_id: str = Field(description="The call id, as the folded line gives it.")

    def run(self, call: ToolCall) -> str:
        earlier = call.session.tool_calls.filter(
            agent=call.agent, call_id=self.call_id, finished_at__isnull=False
        ).first()
        if earlier is None:
            raise Refused(f"none of your finished tool calls has the call id {self.call_id}.")
        return f"Your {earlier.tool} call answered:\n{earlier.result}"


def assemble_the_ad(session: Session, job: Job, made_for: str = "") -> tuple[ProducedItem, bool]:
    """Assemble the finished ad from every scene's clip and the music, and show it in the
    chat, unless it was already assembled from them: then it is shown if it isn't yet, and
    nothing is made again. Gives the ad, and whether it was already assembled. Refuses,
    saying why, until every scene is finished and the music is made. Costs nothing. A fix
    plan's ad is shown labelled with what it was `made_for`, as "scene 2 v2 black toilet"."""
    scenes = list(job.scenes.all())
    being_made = [
        scene.number
        for scene in scenes
        if scene.steps.filter(
            kind__in=[SceneStep.Kind.CLIP, SceneStep.Kind.SCENE_AGENT],
            status=SceneStep.Status.RUNNING,
        ).exists()
    ]
    if being_made:
        is_, it = ("clip is", "it's") if len(being_made) == 1 else ("clips are", "they're")
        raise Refused(
            f"{_scenes(being_made)}'s {is_} still being made. You'll be told when {it} "
            "ready; assemble the ad then."
        )
    unfinished = [scene.number for scene in scenes if scene.status != Scene.Status.FINISHED]
    if unfinished:
        isnt, them = ("isn't", "it") if len(unfinished) == 1 else ("aren't", "them")
        raise Refused(
            f"{_scenes(unfinished)} {isnt} finished: a scene is finished once its clip is "
            f"made. Finish {them}, then assemble the ad."
        )
    music = latest(job, ProducedItem.Kind.MUSIC)
    if music is None:
        raise Refused(
            "the music hasn't been made yet, and it plays under the ad's voice. Create the "
            "music, then assemble the ad."
        )
    scene_clips = [_clip_and_transcript(scene) for scene in scenes]
    # An ad made from the same clips, overlays, music and end card would be this one again.
    # One made before end cards has none, so it is made again with one.
    made_from = [
        (clip.pk, scene.overlay) for scene, (clip, _) in zip(scenes, scene_clips, strict=True)
    ]
    card = _end_card_text(job)
    for ad in job.produced.filter(kind=ProducedItem.Kind.FINISHED_AD).order_by("-version"):
        was_made_from = [(cut["clip"], cut.get("overlay")) for cut in ad.cuts]
        if (
            was_made_from == made_from
            and ad.made_from_id == music.pk
            and ad.end_card is not None
            and _end_card_text_of(ad.end_card) == card
        ):
            _show(session, ad, made_for)
            return ad, True
    try:
        ad = assemble_ad(job, scene_clips, music)
    except AssemblyFailed as failed:
        post_notice(
            job,
            notices.ASSEMBLY_FAILED,
            f"ffmpeg couldn't put the ad together from scenes "
            f"{', '.join(str(scene.number) for scene in scenes)}' clips and the music, so no "
            f"new ad was made and the producer was told it failed. ffmpeg said: {failed}",
            Message.Level.PROBLEM,
        )
        raise Failed(
            "the clips and music couldn't be put together into the ad, as ffmpeg failed: "
            f"{_last_line(str(failed))} Nothing was paid for, and every clip and the music "
            "are kept."
            + (
                ""
                if isinstance(failed, AssemblyTimedOut)
                else " Assembling the same clips again fails the same way."
            )
        ) from failed
    _show(session, ad, made_for)
    return ad, False


def _end_card_text(job: Job) -> tuple[str, ...]:
    """What `job`'s end card would say now: its name, line, price and website."""
    return tuple(
        " ".join(text.split())
        for text in (
            job.product_name,
            job.end_card_line,
            job.end_card_price,
            website_of(job.product_url),
        )
    )


def _end_card_text_of(card: dict[str, Any]) -> tuple[str, ...]:
    """What a finished ad's kept end card says: its name, line, price and website."""
    return tuple(card[part] for part in ("name", "line", "price", "website"))


def _last_line(said: str) -> str:
    """The last line of what a tool said, cut to a sentence's length, for the producer."""
    last = said.strip().splitlines()[-1] if said.strip() else "it gave no reason"
    return last[:300].rstrip(".") + "."


def _photos_in_use(scene: Scene) -> QuerySet[ReferencePhoto]:
    """The shop owner's photos the scene uses now, oldest first."""
    return scene.photos_in_use()


def _photos_of(step: SceneStep) -> set[tuple[str, str]]:
    """The shop owner's photos a step was made with, as (file, thing)."""
    return {(row.file, row.thing.casefold()) for row in step.reference_photos.all()}


# What the producer calls each kind of step, in what it is told.
STEP_NAMES = {
    SceneStep.Kind.SCENE_AGENT: "scene agent",
    SceneStep.Kind.STARTING_PICTURE: "starting picture",
    SceneStep.Kind.LINE_AUDIO: "line's audio",
    SceneStep.Kind.TRANSCRIPT: "transcript",
    SceneStep.Kind.CLIP: "clip",
}


def _clip_and_transcript(scene: Scene) -> tuple[ProducedItem, ProducedItem | None]:
    """The current clip and its transcript: from the clip for H3, its line audio for older
    clips. Clips can play without captions when transcription failed.
    """
    clip = (
        scene.produced.filter(
            kind=ProducedItem.Kind.CLIP,
            step__status=SceneStep.Status.FINISHED,
            **SceneStep.made_for(scene, through="step__"),
        )
        .order_by("version")
        .last()
    )
    assert clip is not None, "a finished scene has a clip of its line"
    transcript = (
        scene.produced.filter(
            kind=ProducedItem.Kind.TRANSCRIPT,
            step__status=SceneStep.Status.FINISHED,
            made_from=clip if clip.made_from_id is None else clip.made_from,
        )
        .order_by("version")
        .last()
    )
    if not stale.current_clip(scene) == clip:
        raise Refused(f"scene {scene.number} needs filming in the current voice first.")
    if clip.made_from_id is None:
        return clip, transcript
    assert transcript is not None, "a clip is only made from audio that was transcribed"
    if not transcript.words:
        raise Refused(
            f"no words were heard in scene {scene.number}'s audio, so there is nothing to cut "
            "its clip to. Make the line's audio again, then its clip."
        )
    return clip, transcript


def _show(session: Session, ad: ProducedItem, made_for: str = "") -> None:
    """Show the finished ad in the chat, unless it already is, as after a restart: with
    what it was made for, if given, so two of them can be told apart."""
    if not Attachment.objects.filter(message__session=session, file=ad.file).exists():
        messages.add(
            session,
            role=Message.Role.AGENT,
            text=f"Ad version {ad.version}, with {made_for}." if made_for else "",
            carrying=[messages.AttachedFile(Attachment.Kind.VIDEO, ad.file)],
        )


def _scenes(numbers: list[int]) -> str:
    """ "scene 2", or "scenes 2 and 3"."""
    if len(numbers) == 1:
        return f"scene {numbers[0]}"
    return f"scenes {_listed([str(number) for number in numbers])}"


def _listed(items: list[str]) -> str:
    """ "a", "a and b", or "a, b and c"."""
    return items[0] if len(items) == 1 else f"{', '.join(items[:-1])} and {items[-1]}"


def _current_clip_in_use(scene: Scene) -> ProducedItem | None:
    """The scene's newest clip, when it is made for the scene as it stands, with the shop
    owner's photos it uses now, and no clip is being made: the clip a scene tool asked for an
    unchanged clip hands back."""
    newest = _latest_clip(scene)
    if (
        newest is None
        or newest != stale.current_clip(scene)
        or scene.steps.filter(
            kind__in=[SceneStep.Kind.CLIP, SceneStep.Kind.SCENE_AGENT],
            status=SceneStep.Status.RUNNING,
        ).exists()
    ):
        return None
    return _clip_with_the_photos_in_use(scene, scene.produced.filter(pk=newest.pk))


def _latest_clip(scene: Scene) -> ProducedItem | None:
    """The scene's newest finished clip, if it has one."""
    return (
        scene.produced.filter(kind=ProducedItem.Kind.CLIP, step__status=SceneStep.Status.FINISHED)
        .order_by("version")
        .last()
    )


def normalised_note(note: str | None) -> str:
    """A note a scene tool is given, as it is kept: its spacing tidied, blank for none, so the
    same words spaced differently are the same note."""
    return " ".join((note or "").split())


def _current_audio(scene: Scene) -> ProducedItem:
    """The scene's audio of its line as it stands, in the person's voice as it stands: the
    only audio a transcript or a clip is made from. Refuses, saying why, if it has none."""
    audios = scene.produced.filter(
        kind=ProducedItem.Kind.LINE_AUDIO, step__status=SceneStep.Status.FINISHED
    ).order_by("version")
    if not audios.exists():
        raise Refused(f"scene {scene.number} has no audio yet. Make the line's audio first.")
    for_the_line = audios.filter(step__line=scene.line)
    if not for_the_line.exists():
        raise Refused(
            f"scene {scene.number}'s audio was made for an earlier line, and the line has "
            "changed since. Make the line's audio again first."
        )
    audio = for_the_line.filter(made_from=latest(scene.job, ProducedItem.Kind.VOICE)).last()
    if audio is None:
        raise Refused(
            f"scene {scene.number}'s audio was made in an earlier voice, and the person has "
            "changed since. Make the line's audio again first."
        )
    return audio


def _start_step(
    call: ToolCall,
    scene: Scene,
    kind: SceneStep.Kind,
    *,
    busy: str,
    note: str = "",
    made_from: ProducedItem | None = None,
    picture: ProducedItem | None = None,
    picture_step: SceneStep | None = None,
    owner_note: str = "",
    fix_goal: str = "",
    look_words: str = "",
) -> None:
    """Start a scene step in the background, from the scene's line, what it shows and its
    B-roll labels as they stand, unless one of its kind is already running for the scene:
    then refuse, saying `busy`. Never while the shop owner's pause holds: the tool was let
    run before it, but a pause pressed since is looked for again here, under the session's
    lock, which the pause takes too, so the step is either refused or there for the pause to
    cancel (spec #247 point 14)."""
    if scene.steps.filter(kind=kind, status=SceneStep.Status.RUNNING).exists():
        raise Refused(busy)
    try:
        with transaction.atomic():
            if Session.objects.select_for_update().get(pk=call.session_id).paused_at:
                raise Refused(loop.PAUSED)
            step = SceneStep.objects.create(
                scene=scene,
                kind=kind,
                tool_call=call,
                **SceneStep.snapshot(scene),
                note=note,
                made_from=made_from,
                picture=picture,
                picture_step=picture_step,
                owner_note=owner_note,
                fix_goal=fix_goal,
                look_words=look_words,
            )
            # The shop owner's photos go with every later step of their scene (D1).
            if scene.shows:
                step.reference_photos.set(_photos_in_use(scene))
            # Together with the step, so the chat is told once whatever happens to it.
            step_signals.say_started(step)
    except IntegrityError:
        # Another started it since the look above.
        raise Refused(busy) from None
    # tasks.py runs the producer, so it imports this module rather than the other way.
    from .tasks import run_scene_step

    transaction.on_commit(lambda: run_scene_step.delay(step.pk))


def _a_checked_scene(call: ToolCall, number: int) -> Scene:
    """The scene a scene tool was asked to work on, once its line has passed the fact check.
    Every scene tool starts here, so none makes anything for a line that hasn't."""
    job = _the_job(call)
    if job is None or not job.scenes.exists():
        raise Refused("the ad hasn't been planned yet, and its scenes come from the plan.")
    scene = job.scenes.filter(number=number).first()
    if scene is None:
        raise Refused(f"the ad has no scene {number}: its scenes are 1 to {job.scenes.count()}.")
    if not scene.fact_checked:
        raise Refused(
            f"scene {number}'s line hasn't passed the fact check, and nothing is made for a "
            "line until it has. Run the planning checks first."
        )
    return scene


def _answered(session: Session, *, since: ToolCall, who_asked: str) -> None:
    """Refuse what needs the shop owner's answer when they haven't answered since `since`
    asked them. `who_asked` says who asked them, and about what."""
    if not session.messages.filter(
        role=Message.Role.USER, created_at__gt=since.finished_at
    ).exists():
        raise Refused(
            f"the shop owner hasn't answered since {who_asked}. Ask them, and wait for their "
            "answer."
        )


def _wrote(session: Session, line: str | None, *, since: ToolCall) -> bool:
    """Whether the shop owner wrote `line` word for word in their reply: a message sent
    since the checks asked them. Only spacing and line breaks may differ. A blank line was
    never written."""
    written = " ".join((line or "").split())
    if not written:
        return False
    replies = session.messages.filter(role=Message.Role.USER, created_at__gt=since.finished_at)
    return any(written in " ".join(text.split()) for text in replies.values_list("text", flat=True))


def _the_job(call: ToolCall) -> Job | None:
    """The session's job, recorded on the checkpoint. For now a session makes one ad, so
    no tool is told which job to work on (#9 lifts this)."""
    job = call.session.jobs.first()
    if job is not None and call.job_id != job.pk:
        call.job = job
        call.save(update_fields=["job"])
    return job


def _page_checked(job: Job) -> bool:
    """Whether the job's page has been read and found to show its product. Only such a page
    is stored with the job."""
    return bool(job.page_html_key)


def _page_read(job: Job) -> bool:
    """Whether the job's page has been read successfully: it was found to show its product,
    and the job has a product photo, from the page or from the shop owner (a picture cut
    from its video isn't enough on its own)."""
    return _page_checked(job) and job.photos.filter(from_video=None).exists()


def _with_its_page(call: ToolCall, needs_it: str) -> Job:
    """The session's job, once its page has been found to show its product. `needs_it` says
    why the tool can't work without it."""
    job = _the_job(call)
    if job is None or not _page_checked(job):
        raise Refused(f"the product page hasn't been read yet, and {needs_it}.")
    return job


def _the_plan(job: Job, reason: str) -> str:
    """The plan as the producer is told it: the scenes and why, and what shows the product
    and presents it."""
    colour_photos = job.photos.filter(shows_product_colour=True).values_list("position", flat=True)
    return "\n".join(
        [
            f"Planned {job.scenes.count()} scenes. {reason}",
            _script(job),
            _overlays(job),
            f"The product's name, said to camera: {job.product_name}",
            f"The product's colour: {job.product_colour}, shown in "
            + (
                f"photos {', '.join(str(number) for number in colour_photos)}."
                if colour_photos
                else "no photo clearly."
            )
            # A job planned before the plan said the size or the gender has neither.
            + (f" Its size: {job.product_size}." if job.product_size else ""),
            "The person: "
            + (f"a {job.person_gender}. " if job.person_gender else "")
            + f"{job.person_looks} Their voice: {job.person_voice}",
        ]
    )


def _spent(job: Job, tool: str) -> Decimal:
    """What every call of `tool` for the job has cost."""
    spent: Decimal | None = ModelCall.objects.filter(
        tool_call__job=job, tool_call__tool=tool
    ).aggregate(cost=Sum("cost_usd"))["cost"]
    return spent or Decimal(0)


def _spent_on_music(job: Job, prompt: str, seconds: int) -> Decimal:
    """What making the job's music as `prompt` asks, `seconds` long, cost."""
    spent: Decimal | None = job.model_calls.filter(
        purpose="make_music", handoff=MusicHandoff(prompt=prompt, seconds=seconds).model_dump()
    ).aggregate(cost=Sum("cost_usd"))["cost"]
    return spent or Decimal(0)


def _not_downloaded(skipped: list[SkippedPhoto]) -> str:
    """The notice for picked photos that couldn't be kept: how many, and which."""
    listed = "\n".join(f"- {photo.url}: {photo.reason}" for photo in skipped)
    return (
        f"{_photos(len(skipped)).capitalize()} picked off the page couldn't be kept, so the ad "
        f"is made without {'them' if len(skipped) != 1 else 'it'}; the rest were kept:\n{listed}"
    )


def _none_kept(skipped: list[SkippedPhoto]) -> str:
    """The notice for when no photo picked off the page could be kept, and the page's
    declared photos are used instead."""
    listed = "".join(f"\n- {photo.url}: {photo.reason}" for photo in skipped)
    return (
        f"None of the {_photos(len(skipped))} picked off the page could be kept. "
        f"{DECLARED_INSTEAD}{listed}"
    )


# Fewer photos than this kept off the page are doubtful: the ad has little to show.
FEWEST_SURE_PHOTOS = 2


def _doubtful(blocked: bool, declared: bool, kept: int) -> str:
    """Why the photos kept off the page are doubtful, or "" if they aren't."""
    declared_ones = (
        "the ones the page declares for search engines, which may show other products or miss "
        "this one."
    )
    if blocked:
        return f"the shop's page blocked our page reader, so the photos are {declared_ones}"
    if declared:
        return f"no photos could be picked off the page, so they are {declared_ones}"
    if kept < FEWEST_SURE_PHOTOS:
        return f"only {_photos(kept)} was found on the page."
    return ""


def _photos(count: int) -> str:
    return f"{count} product photo{'s' if count != 1 else ''}"


def _script(job: Job) -> str:
    """Each scene's line, and what a scene shows while the voice says it, if it doesn't show
    the person talking."""
    return "The script:\n" + "\n".join(
        f"{scene.number}. {scene.line}"
        + (f" (Shows, while the voice says it: {scene.shows})" if scene.shows else "")
        for scene in job.scenes.all()
    )


def _overlays(job: Job) -> str:
    """Each scene's overlay, for the scenes that have one."""
    overlays = [
        f'scene {scene.number} "{scene.overlay}"' for scene in job.scenes.all() if scene.overlay
    ]
    return (
        f"Overlays (code draws these on the finished ad): {', '.join(overlays)}."
        if overlays
        else "Overlays: none."
    )


PRODUCER = Agent(
    name="producer",
    purpose="produce",
    instructions=INSTRUCTIONS,
    tools=[
        ReadPage,
        UsePhotos,
        AddToLibrary,
        PlanAd,
        CreatePerson,
        RemakePerson,
        RunPlanningChecks,
        CreateMusic,
        EditScene,
        AddScene,
        RemoveScene,
        ReorderScenes,
        SetPlace,
        MakePicture,
        StartSceneAgent,
        PassNote,
        KeepTry,
        UseVersion,
        SetReferences,
        ApproveOwnerPart,
        CorrectOwnerPart,
        AskOwner,
        AssembleAd,
        CostSoFar,
        Look,
        ReadAd,
        ReadEarlierCall,
    ],
    # What code tells it of a pasted page, of the shop owner's pause and of a photo sent with
    # a scene's note it went to end its turn without using.
    reads_system_notes=True,
    asks_why=True,
    pausable=True,
    folds=True,
    asks_again=True,
    snapshot=snapshot.ad_as_it_stands,
    holds_the_turn=note_photos.hold_the_turn,
)
