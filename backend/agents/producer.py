"""The producer: the agent the user talks to. What it is told to do, and its tools."""

import re
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Literal, NamedTuple

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.db import IntegrityError, transaction
from django.db.models import Avg, Max, Q, QuerySet, Sum
from django.utils import timezone
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from adforge.retry import OutsideServiceDown
from chat import messages
from chat.models import Attachment, Message, Session
from gateway import catalog
from gateway.models import ModelCall
from gateway.types import MusicHandoff, UnusableReply
from jobs import (
    clip_judge,
    cut_and_send,
    fix_check,
    owner_answers,
    owner_parts,
    page,
    picture_check,
    plan_edits,
    reference_photos,
    scene_references,
    stale,
)
from jobs.checks import LONGEST_LINE_SECONDS, line_seconds
from jobs.library import library_of
from jobs.library_items import add_kept_photos, add_product_only_versions, library_line
from jobs.made_pictures import PictureNotMade, make_picture
from jobs.models import (
    BROLL_FIELDS,
    CLIP_ONLY_INPUTS,
    PLANNED_FOR_THE_ACTION,
    AgentFix,
    Fix,
    Job,
    OwnerQuestion,
    ProducedItem,
    ProductPhoto,
    ReferencePhoto,
    Scene,
    SceneStep,
)
from jobs.notices import post_notice
from jobs.planning import (
    BrollKind,
    Part,
    PersonGender,
    PersonShown,
    ScenePicture,
    ScenePiece,
    pictures_missing,
    piece_missing,
)
from jobs.product_videos import save_videos
from jobs.reference_photos import ReferencePhotoUnusable
from jobs.watcher import watch_videos
from jobs.work import (
    DECLARED_INSTEAD,
    NoPlaceYet,
    NoPresenterYet,
    NoSuchVersion,
    SceneBusy,
    SkippedPhoto,
    TryNotOnOffer,
    assemble_ad,
    bring_back_clip,
    bring_back_picture,
    check_page,
    clip_estimate_usd,
    copy_and_pick,
    create_music,
    create_person,
    drawn_for,
    edit_scene_place,
    fetch_page,
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
    open_question,
    plan,
    remake_person,
    run_checks,
    save_photos,
    scene_place,
    show_once,
    tries_shown,
    try_name,
    use_music_again,
    use_try,
    version_label,
    versions,
    why_the_checks_havent_passed,
    why_the_checks_passed,
)

from . import hard_list, loop, snapshot, step_signals
from .fixing_agent import AGENT as FIXING_AGENT
from .fixing_agent import MOST_TRIES, PLACE_NAMES, carry_on_after_the_pause
from .fixing_agent import PURPOSE as FIXING_AGENT_PURPOSE
from .look import Look
from .loop import Agent, Failed, Refused, Tool
from .models import ToolCall
from .money import dollars

# What the chat shows the shop owner when the product page can't be read: plain words, as
# the technical reason is the producer's, in its tool's result.
PAGE_NOT_OPENED = "We couldn't open this page from its link."
PAGE_TEXT_UNUSABLE = "This page opened, but its text can't be used."
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
the line over it: the plan says which. They are made with the same tools, in the same \
order. Never tell the shop owner which scenes are which, or that there are two kinds.
You work by calling tools. Call one when you need it, read what it hands back, and decide \
what to do next. Before a tool that takes a while, say in one short sentence what you're \
about to do. When there is nothing left to do, reply to them; when you need the shop \
owner, ask them with ask_owner.
The usual order is: read the page, plan the ad, create the person, run the planning \
checks, create the music, then, for each scene, make its starting picture, when it has \
one, and its line's audio, which can run at the same time, transcribe the audio once it's \
ready, and once the picture is made and the audio heard, make the scene's clip, which \
finishes the scene. A scene that shows the product has no starting picture: its clip is made \
once its audio is heard. \
Once every scene is finished, assemble the ad, which shows it to the shop owner. When a \
tool hands back something to ask the shop owner, ask it with ask_owner and wait for their \
answer before passing their choice to a tool.
The plan can change at any step: edit_scene changes any of a scene's fields, and \
add_scene, remove_scene and reorder_scenes change which scenes play and in what order. \
Each says which steps the change made stale; nothing is made again by itself. Run the \
planning checks on what changed, make again only the stale steps the change needs with the \
scene tools, then assemble the ad. Once it is shown, tell the shop owner in one line what \
changed and what it cost.
Every version of a scene is kept. use_version puts an earlier version back in the ad for \
nothing, and keep_try puts in one of the tries the fixing agent filmed of a clip; once an ad \
has been shown, each assembles it again. create_music again in another mood makes new \
music for the ad; assemble the ad again to put it in. cost_so_far says what the ad has \
cost so far, by part.
The person and the place can change at any step too: remake_person makes the presenter \
again, their look, their voice or both, with your instruction on top of the standard \
prompt; set_place gives a scene that shows the product a place of its own, drawn new or a \
small edit of the one it has. Each says which steps the change made stale. Look at a new \
portrait or place with look before making anything from it.
Scene tools start their work in the background and hand back at once, before anything is \
made. Tell the shop owner the work has started, then carry on or reply: don't call the tool \
again to see whether it has finished. When a step finishes or fails, you are told in a \
message from the system, not from the shop owner. Then tell the shop owner what was made, \
or what went wrong.
The shop owner can press pause. A message from the app then says which steps it cancelled \
before they started, with what each was for, and which are still running and will finish. \
Start nothing while paused: every tool that costs money answers paused. If the shop owner hasn't \
written since, tell them in one line that you paused and what is still finishing, then \
wait. Their next message ends the pause: act on it, and call again the tools still needed.
Every fact about the product comes from its page or from the shop owner. You have no tool \
that searches the web, so never look anything up, infer or guess. When something you need \
is missing or unclear, ask the shop owner one short, specific question with ask_owner. \
When you can't do something, say so plainly and say why.
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
When a tool skips a product photo, tell the shop owner which photo and why. When a tool \
fails or refuses, tell the shop owner honestly what happened.
Once a scene has a clip, the app takes the shop owner's notes on the ad: another AI \
reads each one and looks at the pictures and clips, then the app shows them a plan in the \
chat with a yes button, and once they say yes it makes every change and shows the new ad. \
You don't decide what changes or make it, and you have no tool for it: never restate a \
note, ask them to confirm it or say yes, or say you'll change something. What reaches you \
from them then is what isn't a note, such as a question: answer it. You are told what the \
app does in messages from the app, not from the shop owner: the plan shown, with each \
scene's change and cost; the changes started; each scene done and what it cost; a check \
that found a change missing; an error; the new ad shown. Answer their questions from \
those, such as what is happening with a scene or what it costs, and don't repeat what the \
chat already showed them. Never say a change is done before the app says so, and never say \
it is right before your look has said so.
A scene may show a part of the product that no photo shows, drawn from the shop owner's \
words, or from a description made up for it when they let us draw it. The part gets a \
picture of its own, shown to them in the chat, and the clip of a scene showing it isn't \
made until they say yes to that picture: ask them with ask_owner, and wait. Only when \
they say yes to that picture in their own words, call approve_owner_part. When they say \
what to change or send a photo of the part, call correct_owner_part, which draws the \
part's picture again and shows it, and ask them again. Never take silence, a question or \
a yes to something else as their yes, and never decide for them. Every other scene goes \
on meanwhile. The waiting scene has no clip yet, so what they say about the part's picture \
is yours to act on, even once other scenes have clips.
You can't see pictures or videos yourself. When you need to know what a scene's clip, an \
earlier version, a library picture or a try of a fix shows, call look with one question: it \
is shown them, with what the clip was sent, and tells you in words what it sees, what is \
wrong, its root cause and a direction. It decides nothing: you do. Before \
the ad is planned, every photo they send is a product photo: if they send one of something \
else, don't add it, and tell them to send it with a note on a scene once the first version \
is made. Slower can't be done: every clip plays at real-time speed.
When the shop owner's note is about what a scene's clip shows, look at that clip first, \
then call fix_scene with your detailed instruction: the fixing agent films the scene again \
until a try passes, and you are told how the fix ended. When it ends waiting on the fixing \
agent's question, ask the shop owner that question with ask_owner; once they answer, call \
fix_scene for that scene again, with what the fix should do with their answer. A fix the \
pause stopped carries on when you call fix_scene for its scene again.
To choose which library pictures and which video moment a scene that shows the product \
sends its next clip, or to keep a library item out of that scene's clips, call \
set_references: its answer says what the next clip is sent and what is left out.
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
            read = fetch_page(job, self.link)
        except page.PageUnreadable as error:
            post_notice(
                job,
                PAGE_NOT_OPENED,
                Message.Level.PROBLEM,
                wants_pasted_page=True,
            )
            raise Failed(f"the page couldn't be read: {error} {PASTE_IN_THE_PANEL}") from error
        download = read.download
        product_page = page.parse(download)
        check = check_page(job, download, product_page)
        if check.decision == "unreadable":
            post_notice(
                job,
                f"{PAGE_TEXT_UNUSABLE} {check.reason}",
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
        except (UnusableReply, OutsideServiceDown) as error:
            raise Failed(
                f"picking this product's own text out of the page failed ({error}), so the "
                "page wasn't read. Tell the shop owner they can ask to read it again."
            ) from error
        for notice, level in picked.notices:
            post_notice(job, notice, level)
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
                post_notice(job, _none_kept(skipped), Message.Level.PROBLEM)
                skipped = save_photos(job, product_page.photo_urls)
                declared = True
            elif skipped:
                post_notice(job, _not_downloaded(skipped), Message.Level.PROBLEM)
        # The job's library starts from the page's photos, labelled, before the videos' look
        # frames join it.
        add_kept_photos(job, product_text=text, product_only=False)
        # The gallery videos the picker picked, each watched for its moments, look frames,
        # colour and words (jobs.watcher). One that can't be watched is told in red.
        save_videos(job, list(picked.videos))
        unread_videos = watch_videos(job, text)
        # Only once every photo is kept: a worker that stops part-way through them leaves the
        # page unread, so the read run again keeps them all rather than the few already kept.
        keep_page(job, download, product_page, text)
        # Each look photo that shows other things gets its product-only version, once the
        # page is kept: it is slow, and a picture model down never stops the page's read.
        add_product_only_versions(job)
        if messages.page_panel_wanted(call.session):
            post_notice(job, PANEL_NOT_NEEDED, Message.Level.INFO, wants_pasted_page=False)
        # The pictures cut from its videos are the planner's, not news to the owner.
        kept = job.photos.filter(from_video=None).count()
        if kept == 0:
            post_notice(
                job,
                "No usable product photo was found on the page, and every scene is made from "
                "one, so the ad can't be made until the shop owner attaches at least one.",
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
        elif why := _doubtful(picked.blocked, declared, kept):
            _show_photos(call, job)
            call.asked_about = "the photos"
            told.append(
                f"Stopped before planning: {why} The kept photos are shown to the shop owner "
                "in the chat. Tell them why in plain words, and ask them to go ahead with "
                "these photos or attach more. Wait for their answer: if they attach photos, "
                "add them with use_photos, then plan."
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
        # Doubtful photos are planned from only once the shop owner has seen them and answered.
        shown = job.tool_calls.filter(tool=ReadPage.name, asked_about="the photos").last()
        if shown is not None:
            _answered(call.session, since=shown, who_asked="they were shown the page's photos")
        # So are too few photos sent with a page pasted in the page panel.
        told = _told_of_too_few_pasted_photos(job)
        if (
            told is not None
            and not call.session.messages.filter(
                role=Message.Role.USER, pasted_page=False, seq__gt=told.seq
            ).exists()
        ):
            raise Refused(
                "the shop owner hasn't answered since they were told they sent too few photos "
                "with their pasted page. Ask them, and wait for their answer."
            )
        # Planning again before the shop owner has answered would pay for the same question.
        asked = job.tool_calls.filter(tool=self.name, asked_about="the plan").last()
        if asked is not None:
            _answered(call.session, since=asked, who_asked="the planner asked them a question")
        decision = plan(job)
        if decision.question is not None:
            call.asked_about = "the plan"
            return (
                f"The ad can't be planned until the shop owner answers: {decision.question} "
                f"Why: {decision.reason} Ask them, and plan again once they have answered. "
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
    """Create the person who presents the ad: a portrait, and a voice made to match whose
    speaking speed is measured by having it read the script. Both are shown to the shop
    owner in the chat as soon as they exist."""

    name = "create_person"

    def run(self, call: ToolCall) -> str:
        job = _the_job(call)
        if job is None or not job.scenes.exists():
            raise Refused("the ad hasn't been planned yet, and the person is made from the plan.")
        voice = _measured_voice(job)
        if voice is not None and latest(job, ProducedItem.Kind.PORTRAIT) is not None:
            return (
                "The person was already made and shown to the shop owner, so nothing was made "
                f"or paid for again. Making them cost {dollars(_spent(job, self.name))}. "
                f"{_speed(voice)} To change them, call remake_person."
            )
        portrait, voice = create_person(job)
        # A run again after a restart doesn't show the person twice.
        if not Attachment.objects.filter(
            message__session=call.session, file=portrait.file
        ).exists():
            messages.add(
                call.session,
                role=Message.Role.AGENT,
                carrying=[
                    messages.AttachedFile(Attachment.Kind.PICTURE, portrait.file),
                    messages.AttachedFile(Attachment.Kind.SOUND, voice.file),
                ],
            )
        return (
            "Made the person, and showed the shop owner their portrait and their voice "
            f"reading the script in the chat. {_speed(voice)}"
        )


class RemakePerson(Tool):
    """Make the person who presents the ad again: their look (a new portrait), their voice (a
    new voice, its speed measured on the script), or both. What you give replaces the plan's
    description of them, which every later picture is made from; your instruction goes to
    the picture model or the voice designer on top of the standard prompt. The new portrait
    and voice are shown to the shop owner in the chat. Hands back what was made, and which of
    the scenes' steps the change made stale: nothing is made again until you call the scene
    tools for it. Look at the new portrait (look, what "person") before you make anything
    from it."""

    name = "remake_person"

    remake: Literal["look", "voice", "both"] = Field(
        description='What to make again: "look" for a new portrait, "voice" for a new voice, '
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
        description="Your detailed instruction to the picture model or the voice designer, "
        "on top of the standard prompt, such as what the shop owner asked for. Null for none.",
    )

    def run(self, call: ToolCall) -> str:
        job = _the_job(call)
        if job is None or latest(job, ProducedItem.Kind.PORTRAIT) is None:
            raise Refused("the person hasn't been made yet: create the person first.")
        look = self.remake in ("look", "both")
        voice = self.remake in ("voice", "both")
        if self.looks is not None and not look:
            raise Refused('new looks are drawn in a new portrait: remake "look" or "both".')
        if self.voice is not None and not voice:
            raise Refused('a new voice description needs a new voice: remake "voice" or "both".')
        changed = {
            field: value.strip() if isinstance(value, str) else value
            for field, value in (
                ("person_gender", self.gender),
                ("person_looks", self.looks),
                ("person_voice", self.voice),
            )
            if value is not None
        }
        if changed:
            Job.objects.filter(pk=job.pk).update(**changed)
            job.refresh_from_db()
        portrait, made_voice = remake_person(
            job, look=look, voice=voice, instruction=self.instruction or "", call=call
        )
        shown = [
            messages.AttachedFile(kind, item.file)
            for kind, item in (
                (Attachment.Kind.PICTURE, portrait),
                (Attachment.Kind.SOUND, made_voice),
            )
            if item is not None and not _shown_before(call.session, item.file)
        ]
        if shown:
            messages.add(call.session, role=Message.Role.AGENT, carrying=shown)
        said = []
        if portrait is not None:
            said.append(
                f"Made the person's look again (portrait version {portrait.version}) and showed "
                'the shop owner in the chat. Look at it (look, what "person") before you make '
                "anything from it."
            )
        if made_voice is not None:
            said.append(
                f"Made the person's voice again (version {made_voice.version}) and had the shop "
                f"owner hear it reading the script in the chat. {_speed(made_voice)}"
            )
        said.append(f"It cost {dollars(call.cost_usd())}.")
        return " ".join(said + _person_stale(job, portrait is not None))


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
        if not scene.shows:
            raise Refused(
                f"scene {scene.number} has the person talking to camera, and its starting "
                "picture is drawn from the portrait and a product photo, with no picture of a "
                "place. Make its starting picture again with a note saying where they are, or "
                "make it a scene that shows the product with edit_scene first."
            )
        drawn = drawn_for(scene.person_shown)
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


class LineChoice(BaseModel):
    """What the shop owner decided about a line the checks asked them about."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    scene: int = Field(description="The number of the scene whose line it is.")
    choice: Literal["keep", "own", "say_it"] = Field(
        description='"keep" to use the line as it is, "own" to use a line the shop owner '
        'wrote, or "say_it" to keep the line but have the person say it to camera rather than '
        "show what the scene shows. Only a scene that shows something can be said instead."
    )
    own_line: str | None = Field(
        description='The shop owner\'s line, word for word as they wrote it, for "own". Null '
        'for "keep".'
    )

    # An "own" choice without the line is refused as unusable, rather than as a line the
    # shop owner never wrote.
    @model_validator(mode="after")
    def _own_comes_with_the_line(self) -> LineChoice:
        if self.choice == "own" and self.own_line is None:
            raise ValueError('an "own" choice needs the shop owner\'s line')
        return self


class RunPlanningChecks(Tool):
    """Check the script before anything is made from it: every line, and what each scene
    shows, against the product page, then each line against how long its scene can last,
    and the whole script against the target length. A line that fails is rewritten,
    shortened or lengthened, and checked again. Hands back what to ask the shop owner when a
    line still fails after 2 rewrites, when a line the person says to camera is still too
    long after 2 shortenings, when the page itself is unclear, or when the script runs over
    the target. Once they have answered, run the checks again with their choices."""

    name = "run_planning_checks"

    line_choices: list[LineChoice] = Field(
        description="What the shop owner chose for each line the checks asked them about. "
        "Empty when there is none."
    )
    length_choice: Literal["shorten", "keep_longer"] | None = Field(
        description="What the shop owner chose when the script ran over the target length: "
        "shorten it to fit, or keep it longer. Null when they haven't been asked."
    )

    def run(self, call: ToolCall) -> str:
        job = _the_job(call)
        if job is None or not job.scenes.exists():
            raise Refused("the ad hasn't been planned yet, and the checks are run on its script.")
        voice = _measured_voice(job)
        if voice is None:
            raise Refused(
                "the person hasn't been made yet, and the length checks need their voice's "
                "measured speed."
            )
        assert voice.words_per_second is not None, "a measured voice has its speed"
        # Only the shop owner makes these choices, so each one is refused until the checks
        # have asked them and they have answered. Nothing is changed unless all are allowed.
        for line_choice in self.line_choices:
            about = f"scene {line_choice.scene}'s line"
            asked = self._asked(job, f"scene {line_choice.scene}")
            if asked is None:
                raise Refused(
                    f"the checks didn't ask the shop owner about {about}. Only a line they "
                    "asked about can be kept or replaced; changing other lines comes later."
                )
            _answered(call.session, since=asked, who_asked=f"the checks asked about {about}")
            if line_choice.choice == "own" and not _wrote(
                call.session, line_choice.own_line, since=asked
            ):
                raise Refused(
                    f'the shop owner never wrote "{line_choice.own_line}" in their reply to '
                    "the question. A line they give is used exactly as they wrote it, so pass "
                    "it word for word, or ask them."
                )
            scene = job.scenes.get(number=line_choice.scene)
            if line_choice.choice == "say_it" and not scene.shows:
                raise Refused(
                    f"the person already says {about} to camera. Keep it, or use the shop "
                    "owner's own line."
                )
            chosen = line_choice.own_line if line_choice.choice == "own" else scene.line
            assert chosen is not None
            if line_seconds(chosen, voice.words_per_second) > LONGEST_LINE_SECONDS:
                raise Refused(
                    f"the line chosen for {about} takes longer to say than a scene can last "
                    f"({LONGEST_LINE_SECONDS} seconds at the voice's speed), so it can't be "
                    "used. Ask the shop owner for a shorter line."
                )
        if self.length_choice is not None:
            asked = self._asked(job, "length")
            if asked is None:
                raise Refused("the checks haven't asked the shop owner about the script's length.")
            _answered(
                call.session, since=asked, who_asked="the checks asked about the script's length"
            )
        for line_choice in self.line_choices:
            scene = job.scenes.get(number=line_choice.scene)
            if line_choice.choice == "own":
                assert line_choice.own_line is not None
                scene.change_line(" ".join(line_choice.own_line.split()))
            if line_choice.choice == "say_it":
                scene.change_line(scene.line, "")
            # The shop owner knows their product: the line they chose isn't checked again.
            scene.fact_checked = True
            scene.save(
                update_fields=[
                    "line",
                    "shortened_from",
                    "shows",
                    *BROLL_FIELDS,
                    *PLANNED_FOR_THE_ACTION,
                    "status",
                    "fact_checked",
                ]
            )
        if self.length_choice is not None:
            job.length_choice = Job.LengthChoice(self.length_choice)
            job.save(update_fields=["length_choice"])
        asking = run_checks(job)
        if asking is None:
            return "\n".join(
                [
                    f"The checks passed. {why_the_checks_passed(job)} The ad is ready to render.",
                    _script(job),
                ]
            )
        shows = asking.scene is not None and bool(asking.scene.shows)
        ask = {
            "unclear_page": "Ask the shop owner, then run the checks again.",
            "line": (
                "Ask the shop owner whether to keep this line and what the ad shows while "
                "it's said, give their own line, or have the person say it to camera instead."
                if shows
                else "Ask the shop owner whether to keep this line or give their own."
            ),
            "line_length": "Ask the shop owner for a shorter line of their own.",
            "length": "Ask the shop owner whether to shorten it to fit, or keep it longer.",
        }[asking.about]
        scene_asked = f"scene {asking.scene.number if asking.scene else ''}"
        call.asked_about = {
            "unclear_page": "the page",
            "line": scene_asked,
            "line_length": scene_asked,
            "length": "length",
        }[asking.about]
        return "\n".join([f"{asking.question} Why: {asking.reason} {ask}", _script(job)])

    def _asked(self, job: Job, about: str) -> ToolCall | None:
        """When the checks last had the shop owner asked about `about`, if they ever did."""
        return job.tool_calls.filter(tool=self.name, asked_about=about).last()


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
        voice = _measured_voice(job)
        assert voice is not None, "the planning checks pass only with the voice measured"
        mood = music_mood(self.mood)
        prompt, seconds = music_prompt(mood), music_seconds(job, voice)
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


# Why a scene that shows the product gets no starting picture: its clip is made from the
# product's pictures and videos (spec #210 point 19).
NO_STARTING_PICTURE = (
    "scene {scene} shows the product, so it has no starting picture: its clip is made from "
    "the product's own pictures and videos. Make its clip once its audio is heard."
)


class MakeStartingPicture(Tool):
    """Start making a scene's starting picture, made from the portrait and the product photo
    that suits the scene best: the person holding the product, or, for a scene that shows
    the product rather than the person talking, what the scene shows. Works in the
    background and hands back at once; you are told when the picture is ready. Only for a
    line that has passed the fact check."""

    name = "make_starting_picture"

    scene: int = Field(description="The number of the scene.")
    note: str | None = Field(
        description="What the picture should show or change, such as what the shop owner "
        "asked for this scene, as a short instruction. Null for nothing."
    )

    def run(self, call: ToolCall) -> str:
        scene = _a_checked_scene(call, self.scene)
        if scene.shows:
            # Spec #210 point 19: a B-roll clip is made from its sent list.
            raise Refused(NO_STARTING_PICTURE.format(scene=scene.number))
        if latest(scene.job, ProducedItem.Kind.PORTRAIT) is None:
            raise Refused(
                "the person hasn't been made yet, and the picture shows them. Create the "
                "person first."
            )
        note = normalised_note(self.note)
        steps = scene.steps.filter(kind=SceneStep.Kind.STARTING_PICTURE)
        # A picture made with other photos of the shop owner's is another picture.
        made = _with_the_photos_in_use(
            scene,
            steps.filter(
                status=SceneStep.Status.FINISHED, note=note, **SceneStep.picture_made_for(scene)
            )
            .filter(produced__isnull=False)
            .distinct(),
        )
        if made is not None:
            return _already_made_picture(call, scene, made)
        _no_clip_being_made(scene)
        _start_step(
            call,
            scene,
            SceneStep.Kind.STARTING_PICTURE,
            busy=f"scene {scene.number}'s starting picture is already being made. You'll be "
            "told when it's ready.",
            note=note,
        )
        return (
            f"Started scene {scene.number}'s starting picture. It isn't made yet: you'll be "
            "told when it's ready."
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
            raise Failed(
                f"the picture wasn't stored, as {not_made}. Change the prompt or the "
                "references and make it again, or go on without it."
            ) from not_made
        return (
            f"Made library item {made.number}, checked once and passed: "
            f"{library_line(made)} Look at it (look, library_item {made.number}) before a "
            "scene's references name it."
        )


def _made_for_its_references(scene: Scene) -> dict[str, Any]:
    """The filter for clips made through "step__" for the scene's references as they stand:
    what only a clip is made from (CLIP_ONLY_INPUTS)."""
    return {
        key: value
        for key, value in SceneStep.made_for(scene, through="step__").items()
        if key.removeprefix("step__").removesuffix("__isnull") in CLIP_ONLY_INPUTS
    }


def _with_the_photos_in_use(scene: Scene, steps: QuerySet[SceneStep]) -> SceneStep | None:
    """The newest of `steps` made with the shop owner's photos the scene uses now, if any."""
    using = {(row.file, row.thing.casefold()) for row in _photos_in_use(scene)}
    return next((step for step in reversed(steps) if _photos_of(step) == using), None)


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


def _already_made_picture(call: ToolCall, scene: Scene, made: SceneStep) -> str:
    """For a starting picture already made for the scene's line with the same note: say so
    if it is the scene's picture, or bring it back as the scene's picture if a newer one was
    made since, with nothing paid again (#143, Julian 2026-10-11, "same inputs, never pay
    twice"). Bringing it back plans a finished scene again: its clip is made from the picture."""
    # A newer picture being made would land on top of it.
    if scene.steps.filter(
        kind=SceneStep.Kind.STARTING_PICTURE, status=SceneStep.Status.RUNNING
    ).exists():
        raise Refused(
            f"scene {scene.number}'s starting picture is already being made. You'll be told "
            "when it's ready."
        )
    assert made.photo is not None, "a finished starting picture was made from a photo"
    earlier = made.produced.order_by("version").last()
    assert earlier is not None, "a finished starting picture step made its picture"
    current = scene.current_picture()
    with_note = "with this note" if made.note else "with no note"
    cost = dollars(made.tool_call.cost_usd())
    photo = f"Photo {made.photo.position} was used: {made.photo_reason}"
    if current is not None and current.step == made:
        # Brought back before a worker stopped, its scene still waits for its clip.
        clip = _latest_clip(scene)
        unclipped = (
            f" Scene {scene.number} is no longer finished: make its clip from this picture."
            if scene.status != Scene.Status.FINISHED
            and clip is not None
            and (clip.picture is None or clip.picture.step != made)
            else ""
        )
        return (
            f"Scene {scene.number}'s starting picture was already made for this line "
            f"{with_note} (version {current.version}), and the shop owner has seen it, so "
            f"nothing was made or paid for again. Making it cost {cost}. {photo}{unclipped}"
        )
    _no_clip_being_made(scene)
    # Shown again together, so the scene's card shows the picture its clip is made from.
    with transaction.atomic():
        again, was_finished = bring_back_picture(earlier)
        messages.add(
            call.session,
            role=Message.Role.AGENT,
            carrying=[
                messages.AttachedFile(
                    Attachment.Kind.PICTURE, again.file, scene.number, version_label(again)
                )
            ],
        )
    unfinished = (
        f" Scene {scene.number} is no longer finished: make its clip from this picture."
        if was_finished
        else ""
    )
    return (
        f"Scene {scene.number}'s starting picture was already made for this line {with_note} "
        f"(version {earlier.version}), so it is the scene's starting picture again, as version "
        f"{again.version}, and nothing was made or paid for again. Making it cost {cost}. "
        f"{photo}{unfinished}"
    )


def _no_clip_being_made(scene: Scene, then: str = "make its starting picture again then") -> None:
    """Refuse what changes a clip's inputs while the scene's clip is being made, such as a new
    starting picture or new references: that clip, made from what came before, would finish
    the scene once the change had un-finished it (#133). `then` says what to do once it's
    ready."""
    if scene.steps.filter(kind=SceneStep.Kind.CLIP, status=SceneStep.Status.RUNNING).exists():
        raise Refused(
            f"scene {scene.number}'s clip is still being made. You'll be told when it's "
            f"ready; {then}."
        )


class MakeLineAudio(Tool):
    """Start making a scene's audio: the person saying the scene's line, as it stands, in
    their voice. Works in the background and hands back at once; you are told when the
    audio is ready. Only for a line that has passed the fact check."""

    name = "make_line_audio"

    scene: int = Field(description="The number of the scene.")

    def run(self, call: ToolCall) -> str:
        scene = _a_checked_scene(call, self.scene)
        voice = latest(scene.job, ProducedItem.Kind.VOICE)
        if voice is None or not voice.voice_id:
            raise Refused(
                "the person hasn't been made yet, and the audio is in their voice. Create the "
                "person first."
            )
        made = (
            scene.produced.filter(
                kind=ProducedItem.Kind.LINE_AUDIO,
                step__status=SceneStep.Status.FINISHED,
                step__line=scene.line,
                made_from=voice,
            )
            .order_by("version")
            .last()
        )
        if made is not None:
            assert made.step is not None, "a line's audio is made by a scene step"
            return (
                f"Scene {scene.number}'s audio was already made for this line in this voice "
                f"(version {made.version}), so nothing was made or paid for again. Making it "
                f"cost {dollars(made.step.tool_call.cost_usd())}."
            )
        _start_step(
            call,
            scene,
            SceneStep.Kind.LINE_AUDIO,
            busy=f"scene {scene.number}'s audio is already being made. You'll be told when "
            "it's ready.",
            made_from=voice,
        )
        return (
            f"Started scene {scene.number}'s audio. It isn't made yet: you'll be told when "
            "it's ready."
        )


class TranscribeLineAudio(Tool):
    """Start transcribing a scene's audio: every word heard, exactly as it was said, with
    when each was said. Works in the background and hands back at once; you are told what
    was heard. Only for audio of the scene's line as it stands, once that audio is ready."""

    name = "transcribe_line_audio"

    scene: int = Field(description="The number of the scene.")

    def run(self, call: ToolCall) -> str:
        scene = _a_checked_scene(call, self.scene)
        if scene.steps.filter(
            kind=SceneStep.Kind.LINE_AUDIO, status=SceneStep.Status.RUNNING
        ).exists():
            raise Refused(
                f"scene {scene.number}'s {STEP_NAMES[SceneStep.Kind.LINE_AUDIO]} is still being "
                "made. You'll be told when it's ready; transcribe it then."
            )
        audio = _current_audio(scene)
        heard = (
            scene.produced.filter(
                kind=ProducedItem.Kind.TRANSCRIPT,
                step__status=SceneStep.Status.FINISHED,
                made_from=audio,
            )
            .order_by("version")
            .last()
        )
        if heard is not None:
            assert heard.step is not None, "a transcript is made by a scene step"
            return (
                f"Scene {scene.number}'s audio (version {audio.version}) was already "
                f"transcribed (version {heard.version}), so nothing was made or paid for "
                f"again. Transcribing it cost {dollars(heard.step.tool_call.cost_usd())}. It "
                f'was heard as: "{heard.text}"'
            )
        _start_step(
            call,
            scene,
            SceneStep.Kind.TRANSCRIPT,
            busy=f"scene {scene.number}'s audio is already being transcribed. You'll be told "
            "what was heard.",
            made_from=audio,
        )
        return (
            f"Started transcribing scene {scene.number}'s audio. It isn't done yet: you'll be "
            "told when it is."
        )


class MakeClip(Tool):
    """Start making a scene's clip: its starting picture animated to speak its line's audio,
    the same audio that was transcribed, or, for a scene that shows the product rather than
    the person talking, filmed from the product's own pictures and videos with that audio
    laid over it. A scene whose clip is made is finished. Works in the background and hands
    back at once; you are told when the clip is ready. Only once its audio is transcribed and,
    for a scene with a starting picture, the picture is made, for the line as it stands."""

    name = "make_clip"

    scene: int = Field(description="The number of the scene.")
    # Defaults to none, so a call without it makes the clip as before.
    note: str | None = Field(
        default=None,
        description="For a scene that shows the product rather than the person talking: what "
        "the clip's movement should change, as one short instruction. The picture and the "
        "audio stay as they are, and only the clip is made again. Null for nothing.",
    )

    def run(self, call: ToolCall) -> str:
        scene = _a_checked_scene(call, self.scene)
        note = normalised_note(self.note)
        if note:
            _refuse_unless_movement_can_change(
                scene,
                talking="Its starting picture or its line can change instead.",
                unlabelled="Its line can change instead.",
            )
        for kind in [
            SceneStep.Kind.STARTING_PICTURE,
            SceneStep.Kind.LINE_AUDIO,
            SceneStep.Kind.TRANSCRIPT,
        ]:
            if scene.steps.filter(kind=kind, status=SceneStep.Status.RUNNING).exists():
                raise Refused(
                    f"scene {scene.number}'s {STEP_NAMES[kind]} is still being made. You'll be "
                    "told when it's ready; make the clip then."
                )
        picture = None if scene.shows else self._starting_picture(scene)
        if scene.shows and latest(scene.job, ProducedItem.Kind.PORTRAIT) is None:
            raise Refused(
                "the person hasn't been made yet, and the clip is sent the presenter in the "
                "ad's place. Create the person first."
            )
        part = hard_list.waiting_owner_part(scene)
        if part is not None:
            # Marks what the producer waits on, as "the photos" does for planning.
            call.asked_about = "the owner part"
            if not owner_parts.picture_is_current(scene):
                # Drawn now, as when the part changed since the plan: the clip still waits.
                return _part_drawn(scene, part, "Its clip isn't made")
            hard_list.refuse_clip_before_the_owners_yes(scene, part, _library_picture(scene))
        audio = _current_audio(scene)
        if not scene.produced.filter(
            kind=ProducedItem.Kind.TRANSCRIPT,
            step__status=SceneStep.Status.FINISHED,
            made_from=audio,
        ).exists():
            raise Refused(
                f"scene {scene.number}'s audio (version {audio.version}) hasn't been "
                "transcribed yet, and a clip is only made from audio that was heard saying the "
                "line. Transcribe it first."
            )
        picture_step = picture.step if picture is not None else None
        assert picture is None or picture_step is not None, "a picture is made by a step"
        made = _clip_with_the_photos_in_use(
            scene,
            scene.produced.filter(
                kind=ProducedItem.Kind.CLIP,
                step__status=SceneStep.Status.FINISHED,
                made_from=audio,
                # Any version of the picture: one brought back is the same picture (#143). A
                # clip made before clips kept their picture step has only its picture. Made
                # from the references as they stand: a new list makes the clip stale. A B-roll
                # clip, made from its sent list, has no picture: any made for the scene's plan
                # as it stands (its references with it) is the same clip.
                **(
                    {"picture__step": picture_step, **_made_for_its_references(scene)}
                    if picture_step is not None
                    else _made_for_the_plan(scene)
                ),
                # With no note, any clip of the same picture and audio is already made, as
                # before notes; a different note is a new clip.
                **({"step__note": note} if note else {}),
            )
            # A try of the fixing agent's is a clip of the step it fixed, never one made here.
            .exclude(step__tool_call__agent=FIXING_AGENT)
            .order_by("version"),
        )
        in_the_ad = _current_clip_in_use(scene) if made is None and not note else None
        if in_the_ad is not None:
            # Nothing it is made from changed (spec #247 point 9): what exists is handed back,
            # whoever made it, as a try the fixing agent kept, which the lookup above leaves out.
            Scene.objects.filter(pk=scene.pk).update(status=Scene.Status.FINISHED)
            return (
                f"Scene {scene.number}'s clip (version {in_the_ad.version}) is already made for "
                "the scene as it stands, so nothing was made or paid for again. Scene "
                f"{scene.number} is finished."
            )
        if made is not None:
            assert made.step is not None, "a clip is made by a scene step"
            made_from = "from this starting picture" if picture is not None else "from this plan"
            with_note = " with this note" if note else ""
            # A clip being made will be the scene's newest, whichever this one is.
            if scene.steps.filter(
                kind=SceneStep.Kind.CLIP, status=SceneStep.Status.RUNNING
            ).exists():
                raise Refused(
                    f"scene {scene.number}'s clip is already being made. You'll be told when "
                    "it's ready."
                )
            # Gone back to an earlier clip, such as an earlier note's, after another was
            # made: it is brought back as the newest version, so the ad uses it, with no
            # video paid again (Julian 2026-10-11, "Reuse A's clip").
            back = ""
            if made != _latest_clip(scene):
                # Shown again together, so the scene's card shows the clip the ad now uses.
                with transaction.atomic():
                    again = bring_back_clip(made)
                    messages.add(
                        call.session,
                        role=Message.Role.AGENT,
                        carrying=[
                            messages.AttachedFile(
                                Attachment.Kind.VIDEO,
                                again.file,
                                scene.number,
                                version_label(again),
                                clip=again,
                            )
                        ],
                    )
                back = f" It is the scene's clip again, as version {again.version}."
            else:
                # Its references set back to those it was made from: it is current again.
                scene.status = Scene.Status.FINISHED
                scene.save(update_fields=["status"])
            return (
                f"Scene {scene.number}'s clip was already made {made_from} and audio"
                f"{with_note} (version {made.version}), so nothing was made or paid for "
                f"again.{back} Making it cost {dollars(made.step.tool_call.cost_usd())}. "
                f"Scene {scene.number} is finished."
            )
        _start_step(
            call,
            scene,
            SceneStep.Kind.CLIP,
            busy=f"scene {scene.number}'s clip is already being made. You'll be told when it's "
            "ready.",
            note=note,
            made_from=audio,
            picture=picture,
            picture_step=picture_step,
        )
        return (
            f"Started scene {scene.number}'s clip. It isn't made yet: you'll be told when it's "
            "ready."
        )

    @staticmethod
    def _starting_picture(scene: Scene) -> ProducedItem:
        """The starting picture a talking scene's clip is made from; refused when there is
        none for the scene as it stands."""
        # A picture step is finished with its picture made. One made before every B-roll
        # scene got a picture may have none: its scene's picture is made again.
        picture_steps = scene.steps.filter(
            kind=SceneStep.Kind.STARTING_PICTURE,
            status=SceneStep.Status.FINISHED,
            produced__isnull=False,
        )
        if not picture_steps.exists():
            raise Refused(
                f"scene {scene.number} has no starting picture yet. Make its starting picture "
                "first."
            )
        picture = scene.current_picture()
        if picture is None:
            raise Refused(
                f"scene {scene.number}'s starting picture was made for an earlier line, or for "
                "what the scene showed before, and the scene has changed since. Make its "
                "starting picture again first."
            )
        missing = owner_parts.photo_drawn_without(scene, picture)
        if missing is not None:
            raise Refused(
                f"scene {scene.number}'s starting picture was drawn before the shop owner's "
                f"photo of the {missing} was kept for it. Make its starting picture again first."
            )
        return picture


def _made_for_the_plan(scene: Scene) -> dict[str, Any]:
    """The filter for clips whose step was made for the scene as it stands, its line aside:
    the audio a clip speaks holds its line."""
    made_for = SceneStep.made_for(scene, through="step__")
    del made_for["step__line"]
    return made_for


class FixScene(Tool):
    """Hand the shop owner's note about what a scene's clip shows to the fixing agent, which
    films the scene again, try after try, until a try passes the clip judge with the change
    made, or its tries run out, when the last try stays in the ad and the shop owner is shown
    every try. Code hands it their note word for word, your instruction and what your last
    look at the scene's clip found, so look first. Works in the background and hands back at
    once; you are told how it ended. Only for a note about what a clip shows: everything else
    you change with the other tools. When the fixing agent's question waits for the shop
    owner, call it again for the scene once they answered, with your instruction: their
    answer, word for word, and what they attached go to the fix, which carries on. Call it
    again too to carry on a fix the shop owner's pause stopped."""

    name = "fix_scene"

    scene: int = Field(description="The number of the scene.")
    instruction: str = Field(
        min_length=1,
        description="What the fixing agent must change, in detail, from the shop owner's note "
        "and your look; or, when handing back their answer or carrying on a paused fix, what it "
        "should do with it.",
    )

    def run(self, call: ToolCall) -> str:
        scene = _a_checked_scene(call, self.scene)
        instruction = self.instruction.strip()
        if not instruction:
            raise Refused("the instruction is blank: say what the fixing agent must change.")
        if not scene.shows:
            raise Refused(
                f"scene {scene.number} is a talking scene, and the fixing agent only films "
                "scenes that show the product: change it with the scene tools instead."
            )
        question = open_question(scene)
        if question is not None:
            return _hand_the_answer_back(call, scene, question, instruction)
        paused = _paused_fix(scene)
        if paused is not None:
            return _carry_on_the_paused_fix(call, scene, paused, instruction)
        noted = _latest_clip(scene)
        if noted is None:
            raise Refused(
                f"scene {scene.number} has no clip yet, so there is nothing to fix: make its clip."
            )
        busy = (
            f"scene {scene.number}'s clip or its fix is already being made. You'll be told when "
            "it ends."
        )
        if scene.steps.filter(kind=SceneStep.Kind.CLIP, status=SceneStep.Status.RUNNING).exists():
            raise Refused(busy)
        note = _owners_note(call, scene)
        if not note:
            raise Refused(
                "the shop owner hasn't written anything yet: fix_scene hands the fixing agent "
                "their note about what the clip shows."
            )
        _start_step(
            call,
            scene,
            SceneStep.Kind.CLIP,
            busy=busy,
            made_from=noted.made_from,
            picture=noted.picture,
            picture_step=noted.picture.step if noted.picture is not None else None,
            owner_note=note,
            fix_goal=instruction,
            look_words=_last_look(call, scene, noted),
        )
        return (
            f"Started scene {scene.number}'s fix: the fixing agent films it again in the "
            "background until a try passes or its tries run out. You'll be told how it ends."
        )


def _owners_note(call: ToolCall, scene: Scene) -> str:
    """The shop owner's latest note, word for word, whatever its age: their newest messages
    before `call`, every one since the producer last spoke to them before the newest, so an
    ad assembled, a clip shown or a try swapped in since never loses it. Blank when they
    wrote nothing."""
    said = call.session.messages.filter(created_at__lte=call.created_at)
    newest = said.filter(role=Message.Role.USER).exclude(text="").order_by("seq").last()
    if newest is None:
        return ""
    # The producer's own words: code's messages carrying a clip or the ad have none.
    answered = (
        said.filter(role=Message.Role.AGENT, seq__lt=newest.seq)
        .exclude(text="")
        .order_by("seq")
        .last()
    )
    written = said.filter(
        role=Message.Role.USER, seq__gt=answered.seq if answered is not None else 0
    ).order_by("seq")
    return "\n".join(each.text.strip() for each in written if each.text.strip())


def _last_look(call: ToolCall, scene: Scene, noted: ProducedItem) -> str:
    """What the producer's newest look at `scene` since its clip was made found, in words;
    blank when it took none."""
    looked = (
        ToolCall.objects.filter(
            session=call.session,
            agent=call.agent,
            tool=Look.name,
            arguments__scene=scene.number,
            answer="made",
            created_at__gt=noted.created_at,
            created_at__lt=call.created_at,
        )
        .order_by("created_at", "id")
        .last()
    )
    return looked.result if looked is not None else ""


def _hand_the_answer_back(
    call: ToolCall, scene: Scene, question: OwnerQuestion, instruction: str
) -> str:
    """Hand the shop owner's answer to the fixing agent's question back to its parked fix
    (spec #247 point 11): every message they wrote since it was asked, word for word, with
    the photos and videos attached, and the producer's `instruction`. The fix carries on in
    the background."""
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
        owner_answers.answer(
            question,
            words=words,
            photos=[each.file for each in files if each.kind == Attachment.Kind.PICTURE],
            videos=[each.file for each in files if each.kind == Attachment.Kind.VIDEO],
            instruction=instruction,
        )
    except ValueError:
        raise Refused(
            f"the shop owner hasn't answered the fixing agent's question about scene "
            f'{scene.number} yet: "{question.question}" Ask them with ask_owner, and call '
            "fix_scene again once they answer."
        ) from None
    except owner_answers.NotWaiting as not_waiting:
        raise Refused(f"the question takes no answer now: {not_waiting}") from None
    return (
        f"Handed the shop owner's answer to scene {scene.number}'s fix: what they attached "
        "goes into the ad's library, and the fixing agent carries on in the background. "
        "You'll be told how it ends."
    )


def _paused_fix(scene: Scene) -> AgentFix | None:
    """The scene's fix the shop owner's pause parked between tries, still waiting, while the
    clip it left in the ad is the scene's newest: a scene changed since moved past it."""
    fixing = (
        AgentFix.objects.filter(step__scene=scene, paused_at__isnull=False)
        .select_related("step")
        .order_by("id")
        .last()
    )
    if fixing is None:
        return None
    made = versions(scene)
    if not made or made[-1].clip is None or made[-1].clip.pk != fixing.kept_id:
        AgentFix.objects.filter(pk=fixing.pk).update(paused_at=None)
        return None
    return fixing


def _carry_on_the_paused_fix(
    call: ToolCall, scene: Scene, fixing: AgentFix, instruction: str
) -> str:
    """Carry on a fix the shop owner's pause parked (spec #247 point 14a): opened again with
    the producer's `instruction`, its step run again in the background, from its checkpoints."""
    if scene.steps.filter(kind=SceneStep.Kind.CLIP, status=SceneStep.Status.RUNNING).exists():
        raise Refused(f"scene {scene.number}'s clip is being made. You'll be told when it ends.")
    with transaction.atomic():
        if Session.objects.select_for_update().get(pk=call.session_id).paused_at:
            raise Refused(loop.PAUSED)
        step = SceneStep.objects.select_for_update().get(pk=fixing.step_id)
        carry_on_after_the_pause(fixing, instruction)
        SceneStep.objects.filter(pk=step.pk).update(
            status=SceneStep.Status.RUNNING,
            reason="",
            result="",
            finished_at=None,
            producer_read_at=None,
            taken_at=None,
            seen_at=timezone.now(),
        )
        post_notice(
            scene.job,
            f"Scene {scene.number}'s fix carries on.",
            Message.Level.INFO,
            step=messages.StepSignal(
                scene.number, Message.StepKind.CLIP, Message.StepState.STARTED
            ),
        )
        from .tasks import run_scene_step

        step_id = step.pk
        transaction.on_commit(lambda: run_scene_step.delay(step_id))
    return (
        f"Scene {scene.number}'s fix carries on in the background from where the pause stopped "
        "it. You'll be told how it ends."
    )


class _SceneFields(Tool):
    """What a plan edit can give a scene, each null to leave it as it is."""

    line: str | None = Field(
        default=None,
        description="The scene's new line, exactly what the person says. Null keeps it.",
    )
    shows: str | None = Field(
        default=None,
        description="What the scene shows while the voice says the line, in plain words, for "
        "a scene that shows the product rather than the person talking; an empty string has "
        "the person say the line to camera. Null keeps it.",
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
        description="The few words drawn along the top while the scene plays; an empty string "
        "for none. Null keeps it.",
    )

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
    """Set what a scene that shows the product sends its next clip, from the ad's picture
    library: its scene pictures by library number, each with what it is for, in your order;
    its video piece by video and moment number; and the library items never sent for this
    scene, whichever tier would take them. Code still sends the product's front photo as
    Image 1, then your pictures, then the drawn picture of the place, then the product's
    other sides, as many pictures in all as the video model takes: the answer says what the
    next clip is sent and what is left out. A new list makes the scene's clip out of date;
    nothing is remade until you make its clip again. Null keeps what the scene has."""

    name = "set_references"

    scene: int = Field(description="The number of the scene.")
    pictures: list[ScenePicture] | None = Field(
        default=None,
        description="The scene pictures, in the order they matter, the whole list: it "
        "replaces the scene's. Empty for none; null keeps the scene's.",
    )
    piece: ScenePiece | None = Field(
        default=None,
        description="The page video moment whose movement the clip copies. Null keeps the "
        "scene's, unless no_piece is true.",
    )
    no_piece: bool = Field(default=False, description="True to send the clip no video piece.")
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
        _no_clip_being_made(scene, then="set its references then")
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
        _refuse_unknown_references(scene, pictures, piece, exclude)
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
        told = [_references_words(scene)]
        if changed:
            # A clip made for the earlier references is stale now (spec #247 point 9).
            told += filter(None, [stale.after_edit(scene)])
        else:
            told.append("Nothing changed: the scene had these references already.")
        told.append(_next_send_words(scene))
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


def _next_send_words(scene: Scene) -> str:
    """What the scene's next clip is sent as its references stand, said in cut-and-send's
    order, and what the cap and the exclude list keep out: the cut, before anything is paid."""
    going = cut_and_send.next_send(scene, person_shown=scene.person_shown)
    images = "; ".join(
        f'Image {image} "{picture.label}" ('
        + (
            f"library item {picture.library_number}"
            if picture.library_number is not None
            else "drawn"
        )
        + f", {cut_and_send.TIER_NAMES[picture.tier]})"
        for image, picture in enumerate(going.pictures, start=1)
    )
    told = [f"Its next clip is sent: {images or 'no pictures'}."]
    if not any(picture.tier == 1 for picture in going.pictures):
        told.append(
            "No photo of the product's front or look is left to send as Image 1, so the video "
            "model sees the product only in the other pictures."
        )
    drawn, place = scene_place(scene, scene.person_shown)
    if place is None:
        told.append(
            f"No picture of {cut_and_send.DRAWN_NAMES[drawn]} is drawn yet; once it is, it is "
            "sent after the scene pictures."
        )
    if going.left_out:
        told.append(hard_list.left_out_answer(scene.number, going.left_out))
    if going.excluded:
        told.append(hard_list.excluded_answer(scene.number, going.excluded))
    return " ".join(told)


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

    def run(self, call: ToolCall) -> str:
        question = self.question.strip()
        if not question:
            raise Refused("the question is blank: say the one thing to ask the shop owner.")
        # A call run again after a restart has asked already.
        if not call.session.messages.filter(
            role=Message.Role.AGENT, text=question, created_at__gte=call.created_at
        ).exists():
            messages.add(call.session, role=Message.Role.AGENT, text=question)
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
    """Assemble the finished ad from every scene's clip, in order, each cut to where its
    words are said so there is no silence between scenes, with captions of the words as
    spoken, the music under the voice and each scene's overlay, and show it to the shop
    owner in the chat. Only once every scene is finished and the music is made. Costs
    nothing."""

    name = "assemble_ad"
    runs_while_paused = True

    def run(self, call: ToolCall) -> str:
        job = _the_job(call)
        if job is None or not job.scenes.exists():
            raise Refused("the ad hasn't been planned yet, and it is assembled from its scenes.")
        from .run_plan import held_on_a_check  # It runs fixes with this module's tools.

        held = sorted({row.scene.number for row in held_on_a_check(call.session)})
        if held:
            # Code assembles the new ad once the shop owner answers (flow step 9).
            raise Refused(
                f"the shop owner hasn't answered the fix check on {_scenes(held)} yet. The app "
                "assembles the new ad once they answer."
            )
        ad, again = assemble_the_ad(call.session, job)
        if again:
            return (
                f"The ad was already assembled from these clips, overlays and this music "
                f"(version {ad.version}, {ad.seconds:g} seconds) and shown to the shop "
                "owner, so nothing was made again. Assembling costs nothing."
            )
        first, *rest = ad.cuts
        plays = [
            f"Scene {first['scene']} plays from {first['start']:g} to {first['end']:g} seconds"
        ]
        plays += [f"scene {cut['scene']} from {cut['start']:g} to {cut['end']:g}" for cut in rest]
        return (
            f"Assembled the ad (version {ad.version}, {ad.seconds:g} seconds) from each scene's "
            "clip, cut to where its words are said, with captions of the words as spoken, the "
            "music under the voice and each scene's overlay, and showed it to the shop owner "
            f"in the chat. {_listed(plays)}.{_spent_since_the_ad_before(call.session, ad)} "
            "Tell the shop owner."
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
    job = _the_job(call)
    if job is None or not job.produced.filter(kind=ProducedItem.Kind.FINISHED_AD).exists():
        return ""
    try:
        ad, _ = assemble_the_ad(call.session, job)
    except Refused as held:
        return f" The ad wasn't assembled again yet: {held} Assemble it once it can be."
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
        parts = [
            *(f"scene {number} {dollars(cost)}" for number, cost in by_scene.items()),
            *(f"{tool} {dollars(cost or Decimal(0))}" for tool, cost in rest),
            f"your own turns {dollars(_summed(paid.filter(purpose=PRODUCER.purpose)))}",
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
        if scene.steps.filter(kind=SceneStep.Kind.CLIP, status=SceneStep.Status.RUNNING).exists()
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
    # An ad made from the same clips, overlays and music would be this one again.
    made_from = [
        (clip.pk, scene.overlay) for scene, (clip, _) in zip(scenes, scene_clips, strict=True)
    ]
    for ad in job.produced.filter(kind=ProducedItem.Kind.FINISHED_AD).order_by("-version"):
        was_made_from = [(cut["clip"], cut.get("overlay")) for cut in ad.cuts]
        if was_made_from == made_from and ad.made_from_id == music.pk:
            _show(session, ad, made_for)
            return ad, True
    ad = assemble_ad(job, scene_clips, music)
    _show(session, ad, made_for)
    return ad, False


def _photos_in_use(scene: Scene) -> QuerySet[ReferencePhoto]:
    """The shop owner's photos the scene uses now, oldest first."""
    return scene.photos_in_use()


def _photos_of(step: SceneStep) -> set[tuple[str, str]]:
    """The shop owner's photos a step was made with, as (file, thing)."""
    return {(row.file, row.thing.casefold()) for row in step.reference_photos.all()}


# What a clean picture of a shop owner's thing is estimated to cost: a picture and its
# check's looks, as a starting picture with none recorded (PICTURE_FALLBACK_USD).
CLEAN_PICTURE_USD = Decimal("0.10")


# What the producer calls each kind of step, in what it is told.
STEP_NAMES = {
    SceneStep.Kind.STARTING_PICTURE: "starting picture",
    SceneStep.Kind.LINE_AUDIO: "line's audio",
    SceneStep.Kind.TRANSCRIPT: "transcript",
    SceneStep.Kind.CLIP: "clip",
}


def quoted_line(fix: str) -> str | None:
    """The new line a fix for a new line holds: the one span of it in straight or curly
    quotes, its spacing tidied. None when it holds no such span, or more than one."""
    spans = re.findall(r'["\u201c]([^"\u201c\u201d]*)["\u201d]', fix)
    return normalised_note(spans[0]) or None if len(spans) == 1 else None


def _seconds_to_say(scene: Scene, line: str) -> float:
    """How long the person's voice takes to say `line`, at its measured speed."""
    voice = _measured_voice(scene.job)
    assert voice is not None and voice.words_per_second is not None, (
        "a scene with a clip was made in a measured voice"
    )
    return line_seconds(line, voice.words_per_second)


def _refuse_unless_movement_can_change(scene: Scene, *, talking: str, unlabelled: str) -> None:
    """Refuses when the scene's clip movement can't be rewritten from a note, ending with what
    can be done instead: `talking` for a talking scene, `unlabelled` for one planned before
    its B-roll labels."""
    if not scene.shows:
        # HeyGen ignored the motion prompt in a talking clip (2026-10-11 test, Julian's
        # grade "no"; runs/heygen-motion-test/ in the project files).
        raise Refused(
            f"scene {scene.number} shows the person talking, and a talking clip's movement "
            "can't be changed from a note: the video model ignores what it's asked about "
            f"movement in a talking clip. {talking}"
        )
    if not scene.broll_kind:
        raise Refused(
            f"scene {scene.number} was planned before scenes had their B-roll labels, so its "
            f"movement can't be rewritten from a note. {unlabelled}"
        )


# What a starting picture is estimated to cost when the scene's last one has no cost
# recorded: a picture (about $0.05) and its judge's looks (about $0.04 each), the costs seen
# on the round 3 test ads (project memory, "Costs").
PICTURE_FALLBACK_USD = Decimal("0.10")

# The ad's before-picture, its room alone and its person-in-room picture are drawn once, by
# the first B-roll starting picture, and later ones reuse them, so a picture made again
# doesn't pay for them.
_DRAWN_ONCE_PURPOSES = [
    "plan_before_picture",
    "make_before_picture",
    "make_room_alone",
    "make_person_in_room",
]


class Estimate(NamedTuple):
    """What a fix is estimated to cost, and the most it can cost: the same, unless the clip
    judge can have its clip made again."""

    about: Decimal
    up_to: Decimal


# What one call to the clip judge, the fix check or the writer of a clip's video prompt is
# estimated to cost when the ad has none recorded: about $0.07, the proof's average for an
# ask of the clip judge on gpt-6.1-sol (decider call 13, 2026-10-13).
CALL_FALLBACK_USD = Decimal("0.07")
MILLIONTH = Decimal("0.000001")
# The clip judge's asks about a B-roll clip, made from its sent list (spec #210 point 19):
# its first question once, then PRODUCT and MOTION each ASKS times.
_JUDGE_PURPOSES = ["check_clip_references", "check_clip_product", "check_clip_motion"]
_JUDGE_ASKS = 1 + 2 * clip_judge.ASKS
_WRITER_PURPOSES = ["rewrite_clip_prompt"]
# The fixing agent's turns for one try: a look or two, then film_again. A GUESS, until real
# runs of the agent in the app are recorded (fixing agent build, T3).
AGENT_TURNS_PER_TRY = 3


def _estimate(scene: Scene, redo: Fix.Redo, fix: str) -> Estimate:
    """What making `redo` again for the scene, for `fix`, is estimated to cost, to the
    millionth of a dollar a bill is recorded to: a price times the seconds a voice was
    measured to take never ends."""
    if redo == Fix.Redo.PICTURE:
        estimate = picture_estimate(scene)
    elif redo == Fix.Redo.LINE:
        estimate = _line_estimate(scene, fix)
    else:
        estimate = _clip_estimate(scene, _clip_seconds(scene), from_note=True)
    return Estimate(*(amount.quantize(MILLIONTH, rounding=ROUND_HALF_UP) for amount in estimate))


def _line_estimate(scene: Scene, fix: str, *, new_picture: bool = False) -> Estimate:
    """What a new line for the scene, then its audio, transcript and clip, is estimated to
    cost, sized from the new line in `fix`, as long as the voice takes to say it. A new line
    keeps the scene's starting picture (Julian, 2026-10-17): with `new_picture`, the shop
    owner also asked for a new one, estimated as for a picture fix."""
    line = quoted_line(fix)
    assert line is not None, "a fix for a new line holds it in quotes"
    seconds = _seconds_to_say(scene, line)
    rest = (
        (_picture_only_estimate_usd(scene) if new_picture else Decimal(0))
        + catalog.speech_cost_usd(catalog.MODEL_FOR_PURPOSE["speak_line"], len(line))
        + catalog.transcription_cost_usd(catalog.MODEL_FOR_PURPOSE["transcribe_line"], seconds)
    )
    clip = _clip_estimate(scene, seconds, from_note=False)
    return Estimate(rest + clip.about, rest + clip.up_to)


def picture_estimate(scene: Scene) -> Estimate:
    """What a new starting picture for the scene, then its clip, is estimated to cost: up to
    the picture drawn again, and checked again, each time its fix check can fail it, when
    checks are on."""
    picture = _picture_only_estimate_usd(scene)
    clip = _clip_estimate(scene, _clip_seconds(scene), from_note=False)
    redraws = fix_check.MOST_REDRAWS if settings.QUALITY_CHECKS else 0
    # The fix check asks as often as the picture check does.
    check = picture_check.ASKS * _each_call_usd(scene, ["check_fix"])
    return Estimate(picture + clip.about, picture + redraws * (picture + check) + clip.up_to)


def _picture_only_estimate_usd(scene: Scene) -> Decimal:
    """What a new starting picture for the scene is estimated to cost: what its last one cost,
    its judge's looks included, or the fallback when that isn't recorded."""
    last = (
        scene.steps.filter(kind=SceneStep.Kind.STARTING_PICTURE, status=SceneStep.Status.FINISHED)
        # A try's picture step is charged its whole try: its clip and judge too.
        .exclude(tool_call__agent=FIXING_AGENT)
        .last()
    )
    paid: Decimal | None = None
    if last is not None:
        paid = last.tool_call.model_calls.exclude(purpose__in=_DRAWN_ONCE_PURPOSES).aggregate(
            cost=Sum("cost_usd")
        )["cost"]
    return paid or PICTURE_FALLBACK_USD


def _clip_seconds(scene: Scene) -> float:
    """How long the audio the scene's clip speaks lasts."""
    clip = _latest_clip(scene)
    assert clip is not None and clip.made_from is not None, "a fix is for a scene with a clip"
    seconds = clip.made_from.seconds
    assert seconds is not None, "a line's audio is measured when it's made"
    return seconds


def _clip_estimate(scene: Scene, audio_seconds: float, *, from_note: bool) -> Estimate:
    """What a new clip of the scene covering `audio_seconds` of its line is estimated to
    cost: its video and, for a B-roll clip with quality checks on, the clip judge's asks; for
    a clip made from the shop owner's note, also the call writing its video prompt; its fix
    is checked by the clip judge's own asks (Julian 2026-10-14). It can cost up to that
    plus every try the fixing agent can film once the judge fails it (door 1)."""
    video = _clip_video_usd(scene, audio_seconds)
    judged = bool(scene.shows) and settings.QUALITY_CHECKS
    about = video + _judge_usd(scene)
    if from_note:
        about += _each_call_usd(scene, _WRITER_PURPOSES)
    tries = MOST_TRIES * agent_try_usd(scene, audio_seconds) if judged else Decimal(0)
    return Estimate(about, about + tries)


def _clip_video_usd(scene: Scene, audio_seconds: float) -> Decimal:
    """What the video of a clip of the scene covering `audio_seconds` is billed: a B-roll
    clip's with the seconds of its planned piece sent beside it."""
    broll = bool(scene.shows)
    return clip_estimate_usd(
        audio_seconds,
        broll=broll,
        piece_seconds=cut_and_send.planned_piece_seconds(scene) if broll else 0.0,
    )


def agent_try_usd(scene: Scene, audio_seconds: float) -> Decimal:
    """What one try of the fixing agent at the scene is estimated to cost, for either door: a
    new starting picture (none for a scene showing the product: its clip is made from the
    product's own pictures, spec #210), its video covering `audio_seconds`, the clip judge's
    asks (which check the shop owner's change too, for a fix) and the agent's turns."""
    return (
        (Decimal(0) if scene.shows else _picture_only_estimate_usd(scene))
        + _clip_video_usd(scene, audio_seconds)
        + _judge_usd(scene)
        + AGENT_TURNS_PER_TRY * _each_call_usd(scene, [FIXING_AGENT_PURPOSE])
    )


def _judge_usd(scene: Scene) -> Decimal:
    """What the clip judge's asks about one of the scene's clips are estimated to cost: none
    for a talking scene, or with quality checks off."""
    if not (scene.shows and settings.QUALITY_CHECKS):
        return Decimal(0)
    return _JUDGE_ASKS * _each_call_usd(scene, _JUDGE_PURPOSES)


def _each_call_usd(scene: Scene, purposes: list[str]) -> Decimal:
    """What one call for one of `purposes` is estimated to cost: the average of those made
    for the scene's last clip, or else of those made for the ad, or the fallback when none
    is recorded."""
    paid = ModelCall.objects.filter(job=scene.job, purpose__in=purposes, cost_usd__gt=0)
    last = (
        scene.steps.filter(kind=SceneStep.Kind.CLIP, status=SceneStep.Status.FINISHED)
        .exclude(tool_call__agent=FIXING_AGENT)
        .last()
    )
    for calls in (paid.filter(tool_call=last.tool_call) if last else paid.none(), paid):
        average: Decimal | None = calls.aggregate(cost=Avg("cost_usd"))["cost"]
        if average:
            return average
    return CALL_FALLBACK_USD


def _use_the_photos(fix: Fix) -> None:
    """Once the shop owner has said yes to the fix, its scene uses its photos from now on
    (D1), each in place of any it used of the same thing (D3), and stops using those it
    drops. A clean picture already drawn of the same thing from the same photo is kept."""
    using = list(_photos_in_use(fix.scene))
    for row in using:
        if any(reference_photos.same_thing(row.thing, thing) for thing in fix.drops) or any(
            reference_photos.same_thing(row.thing, str(photo["thing"])) for photo in fix.photos
        ):
            row.dropped_by = fix
            row.save(update_fields=["dropped_by"])
    for photo in fix.photos:
        drawn = (
            ReferencePhoto.objects.filter(
                scene__job=fix.job, file=photo["file"], thing__iexact=photo["thing"]
            )
            .exclude(drawn="")
            .values_list("drawn", flat=True)
            .first()
        )
        ReferencePhoto.objects.create(
            scene=fix.scene,
            file=photo["file"],
            thing=photo["thing"],
            use=photo["use"],
            drawn=drawn or "",
            added_by=fix,
        )


def _clip_and_transcript(scene: Scene) -> tuple[ProducedItem, ProducedItem]:
    """A finished scene's clip of its line as it stands, and the transcript of the audio the
    clip speaks, whose words say where to cut it. Refuses, saying why, if no word was heard."""
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
            made_from=clip.made_from,
        )
        .order_by("version")
        .last()
    )
    assert transcript is not None, "a clip is only made from audio that was transcribed"
    if not transcript.words:
        raise Refused(
            f"no words were heard in scene {scene.number}'s audio, so there is nothing to cut "
            "its clip to. Make the line's audio again, then its clip."
        )
    return clip, transcript


def _show(session: Session, ad: ProducedItem, made_for: str = "") -> None:
    """Show the finished ad in the chat, unless it already is, as after a restart: with
    what it was made for, if a fix plan made it, so two of them can be told apart."""
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
        or scene.steps.filter(kind=SceneStep.Kind.CLIP, status=SceneStep.Status.RUNNING).exists()
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


def _measured_voice(job: Job) -> ProducedItem | None:
    """The person's voice, once its speaking speed has been measured."""
    voice = latest(job, ProducedItem.Kind.VOICE)
    return voice if voice is not None and voice.words_per_second is not None else None


def _speed(voice: ProducedItem) -> str:
    return f"The voice speaks {voice.words_per_second:.1f} words a second, measured on the script."


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


def _told_of_too_few_pasted_photos(job: Job) -> Message | None:
    """Code's notice that a page pasted in the page panel was kept as the job's page, if
    fewer than FEWEST_SURE_PHOTOS of its photos were kept as product photos: only what the
    shop owner writes after it answers the question of too few photos. A message they typed
    while the paste was checked, or another paste, doesn't."""
    pasted = _pasted_with_too_few_photos(job)
    if pasted is None or job.session is None:
        return None
    return job.session.messages.filter(
        role=Message.Role.NOTICE, pasted_page_result=messages.PASTE_ACCEPTED, seq__gt=pasted.seq
    ).first()


def _pasted_with_too_few_photos(job: Job) -> Message | None:
    """The page panel's message whose pasted page was kept as the job's page, if fewer than
    FEWEST_SURE_PHOTOS of its photos were kept as product photos."""
    if job.session is None:
        # A job started before sessions existed has no chat to paste in.
        return None
    pasted = job.session.messages.filter(
        role=Message.Role.USER, pasted_page=True, pasted_page_result=messages.PASTE_ACCEPTED
    ).last()
    if pasted is None:
        return None
    sent = pasted.attachments.values_list("file", flat=True)
    return pasted if job.photos.filter(file__in=sent).count() < FEWEST_SURE_PHOTOS else None


def _show_photos(call: ToolCall, job: Job) -> None:
    """Show the shop owner the job's photos in the chat, as pictures on the agent's message:
    those from the page and their own, not the ones cut from the product's videos."""
    messages.add(
        call.session,
        role=Message.Role.AGENT,
        carrying=[
            messages.AttachedFile(Attachment.Kind.PICTURE, photo.file)
            for photo in job.photos.filter(from_video=None)
        ],
    )


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
    return f"Overlays: {', '.join(overlays)}." if overlays else "Overlays: none."


PRODUCER = Agent(
    name="producer",
    purpose="produce",
    instructions=INSTRUCTIONS,
    tools=[
        ReadPage,
        UsePhotos,
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
        MakeStartingPicture,
        MakePicture,
        MakeLineAudio,
        TranscribeLineAudio,
        MakeClip,
        FixScene,
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
    # What code does with the fix plans it runs reaches the producer only so (decision 20).
    reads_system_notes=True,
    asks_why=True,
    pausable=True,
    folds=True,
    snapshot=snapshot.ad_as_it_stands,
)
