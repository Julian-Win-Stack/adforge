"""The producer: the agent the user talks to. What it is told to do, and its tools."""

import re
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal, NamedTuple

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.db import IntegrityError, transaction
from django.db.models import Avg, Max, QuerySet, Sum
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from adforge.retry import OutsideServiceDown
from chat import messages
from chat.models import Attachment, Message, Session
from gateway import catalog
from gateway.models import ModelCall
from gateway.types import MusicHandoff, UnusableReply
from jobs import auto_fix, clip_judge, fix_check, page, picture_check, reference_photos
from jobs.checks import LONGEST_LINE_SECONDS, line_seconds
from jobs.models import (
    BROLL_FIELDS,
    Fix,
    Job,
    ProducedItem,
    ProductPhoto,
    ReferencePhoto,
    Scene,
    SceneStep,
)
from jobs.notices import post_notice
from jobs.work import (
    DECLARED_INSTEAD,
    SkippedPhoto,
    assemble_ad,
    bring_back_clip,
    bring_back_picture,
    check_page,
    clip_estimate_usd,
    copy_and_pick,
    create_music,
    create_person,
    fetch_page,
    keep_page,
    latest,
    music_mood,
    music_prompt,
    music_seconds,
    note_face,
    plan,
    run_checks,
    save_photos,
    use_music_again,
    version_label,
    why_the_checks_havent_passed,
    why_the_checks_passed,
)

from . import step_signals
from .loop import Agent, Refused, Tool
from .models import ToolCall
from .money import dollars

# What the producer is told when the product page can't be read, after why: the chat has asked
# the shop owner for the page panel (ADR 0006).
# What the chat shows the shop owner when the product page can't be read: plain words, as
# the technical reason is the producer's, in its tool's result.
PAGE_NOT_OPENED = "We couldn't open this page from its link."
PAGE_TEXT_UNUSABLE = "This page opened, but its text can't be used."
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
about to do. When there is nothing left to do, or you need the shop owner, reply to them.
The usual order is: read the page, plan the ad, create the person, run the planning \
checks, create the music, then, for each scene, make its starting picture and its line's \
audio, which can run at the same time, transcribe the audio once it's ready, and once the \
picture is made and the audio heard, make the scene's clip, which finishes the scene. \
Once every scene is finished, assemble the ad, which shows it to the shop owner. When a \
tool hands back something to ask the shop owner, ask it in your reply and wait for their \
answer before passing their choice to a tool.
Scene tools start their work in the background and hand back at once, before anything is \
made. Tell the shop owner the work has started, then carry on or reply: don't call the tool \
again to see whether it has finished. When a step finishes or fails, you are told in a \
message from the system, not from the shop owner. Then tell the shop owner what was made, \
or what went wrong.
Every fact about the product comes from its page or from the shop owner. You have no tool \
that searches the web, so never look anything up, infer or guess. When something you need \
is missing or unclear, ask one short, specific question. When you can't do something, say \
so plainly and say why.
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
it is right: you can't see pictures or videos.
What you can't do, said plainly the same way each time: see pictures or videos. Before \
the ad is planned, every photo they send is a product photo: if they send one of something \
else, don't add it, and tell them to send it with a note on a scene once the first version \
is made. Slower can't be done: every clip plays at real-time speed.
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
        if job is None:
            job = Job.objects.create(
                session=call.session, product_url=self.link, target_seconds=self.target_seconds
            )
            call.job = job
            call.save(update_fields=["job"])
        elif _page_read(job):
            if self.link != job.product_url:
                raise Refused(
                    f"this chat's ad is for {job.product_url}, whose page has already been "
                    f"read, and {self.link} is a different link. Ads for other products come "
                    "later: tell the shop owner to start a new chat for this one for now."
                )
            return (
                f"{self.link} was already read for this ad, so nothing was read or paid for "
                f"again. Reading it cost {dollars(_spent(job, self.name))}. The ad has "
                f"{_photos(job.photos.count())}."
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
            return f"The page couldn't be read: {error} {PASTE_IN_THE_PANEL}"
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
            return f"The page was read but can't be used: {check.reason} {PASTE_IN_THE_PANEL}"
        # Before the photos: a page whose text can't be copied isn't read, and keeps nothing.
        # The photos are picked while the text is copied.
        try:
            text, picked = copy_and_pick(job, read, product_page)
        except (UnusableReply, OutsideServiceDown) as error:
            return (
                f"Failed: picking this product's own text out of the page failed ({error}), so "
                "the page wasn't read. Tell the shop owner they can ask to read it again."
            )
        for notice, level in picked.notices:
            post_notice(job, notice, level)
        # Without Firecrawl set up nothing is picked off the page, which is a setup mistake
        # the notice already names, not a doubt about this page's photos.
        declared = picked.urls is None and read.marked is not None
        if picked.urls is None:
            skipped = save_photos(job, product_page.photo_urls)
        else:
            skipped = save_photos(job, picked.urls, merge_copies=True, faces=picked.faces)
            if skipped and not job.photos.exclude(source_url="").exists():
                post_notice(job, _none_kept(skipped), Message.Level.PROBLEM)
                skipped = save_photos(job, product_page.photo_urls)
                declared = True
            elif skipped:
                post_notice(job, _not_downloaded(skipped), Message.Level.PROBLEM)
        # Only once every photo is kept: a worker that stops part-way through them leaves the
        # page unread, so the read run again keeps them all rather than the few already kept.
        keep_page(job, download, product_page, text)
        if messages.page_panel_wanted(call.session):
            post_notice(job, PANEL_NOT_NEEDED, Message.Level.INFO, wants_pasted_page=False)
        kept = job.photos.count()
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
        count = job.photos.count()
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
        if not job.photos.exists():
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
        return _the_plan(job, decision.reason)


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
                f"{_speed(voice)}"
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
            steps.filter(status=SceneStep.Status.FINISHED, note=note, **SceneStep.made_for(scene))
            .filter(produced__isnull=False)
            .distinct(),
        )
        if made is not None:
            return _already_made_picture(call, scene, made)
        _no_clip_being_made(scene)
        _before_its_clip(scene)
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
    twice"). Bringing it back changes the ad, so it is refused once the scene has a clip."""
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
    current = _current_picture(scene)
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
    _before_its_clip(scene)
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


def _no_clip_being_made(scene: Scene) -> None:
    """Refuse a new starting picture while the scene's clip is being made: that clip, of the
    picture before, would finish the scene once the new picture had un-finished it (#133)."""
    if scene.steps.filter(kind=SceneStep.Kind.CLIP, status=SceneStep.Status.RUNNING).exists():
        raise Refused(
            f"scene {scene.number}'s clip is still being made. You'll be told when it's "
            "ready; make its starting picture again then."
        )


def _current_picture(scene: Scene) -> ProducedItem | None:
    """The starting picture the scene's clip is made from: the newest version made for what
    the scene shows as it stands, or brought back (#143). A line shortened to fit its clip
    still shows what its picture was made for."""
    made_for = SceneStep.made_for(scene, through="step__")
    del made_for["step__line"]
    return (
        scene.produced.filter(
            kind=ProducedItem.Kind.STARTING_PICTURE,
            step__kind=SceneStep.Kind.STARTING_PICTURE,
            step__status=SceneStep.Status.FINISHED,
            step__line__in=[scene.line, *scene.shortened_from],
            **made_for,
        )
        .order_by("version")
        .last()
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
        _before_its_clip(scene)
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
        _before_its_clip(scene)
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
    the person talking, animated with that audio laid over it. A scene whose clip is made is
    finished. Works in the
    background and hands back at once; you are told when the clip is ready. Only once the
    scene's starting picture is made and its audio transcribed, for the line as it stands."""

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
                unlabelled="Make its starting picture again with the note instead.",
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
        picture = _current_picture(scene)
        if picture is None:
            raise Refused(
                f"scene {scene.number}'s starting picture was made for an earlier line, or for "
                "what the scene showed before, and the scene has changed since. Make its "
                "starting picture again first."
            )
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
        picture_step = picture.step
        assert picture_step is not None, "a starting picture is made by a scene step"
        made = _clip_with_the_photos_in_use(
            scene,
            scene.produced.filter(
                kind=ProducedItem.Kind.CLIP,
                step__status=SceneStep.Status.FINISHED,
                made_from=audio,
                # Any version of the picture: one brought back is the same picture (#143). A
                # clip made before clips kept their picture step has only its picture.
                picture__step=picture_step,
                # With no note, any clip of the same picture and audio is already made, as
                # before notes; a different note is a new clip.
                **({"step__note": note} if note else {}),
            ).order_by("version"),
        )
        if made is not None:
            assert made.step is not None, "a clip is made by a scene step"
            made_from = "from this starting picture"
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
                _before_its_clip(scene)
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
                            )
                        ],
                    )
                back = f" It is the scene's clip again, as version {again.version}."
            return (
                f"Scene {scene.number}'s clip was already made {made_from} and audio"
                f"{with_note} (version {made.version}), so nothing was made or paid for "
                f"again.{back} Making it cost {dollars(made.step.tool_call.cost_usd())}. "
                f"Scene {scene.number} is finished."
            )
        _before_its_clip(scene)
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


class AssembleAd(Tool):
    """Assemble the finished ad from every scene's clip, in order, each cut to where its
    words are said so there is no silence between scenes, with captions of the words as
    spoken, the music under the voice and each scene's overlay, and show it to the shop
    owner in the chat. Only once every scene is finished and the music is made. Costs
    nothing."""

    name = "assemble_ad"

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
            f"in the chat. {_listed(plays)}. Tell the shop owner."
        )


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

# The ad's before-picture is drawn once, by the first B-roll starting picture, and later ones
# reuse it, so a picture made again doesn't pay for it.
_BEFORE_PICTURE_PURPOSES = ["plan_before_picture", "make_before_picture"]


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
_JUDGE_PURPOSES = ["check_clip_sides", "check_clip_motion"]
_WRITER_PURPOSES = ["rewrite_clip_prompt", "remake_clip_prompt"]


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
    last = scene.steps.filter(
        kind=SceneStep.Kind.STARTING_PICTURE, status=SceneStep.Status.FINISHED
    ).last()
    paid: Decimal | None = None
    if last is not None:
        paid = last.tool_call.model_calls.exclude(purpose__in=_BEFORE_PICTURE_PURPOSES).aggregate(
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
    plus, for each remake the clip judge can have made, its video, the judge's asks and the
    call writing its prompt (decider call 13, 2026-10-13)."""
    video = clip_estimate_usd(audio_seconds, broll=bool(scene.shows))
    judged = bool(scene.shows) and settings.QUALITY_CHECKS
    judge = 2 * clip_judge.ASKS * _each_call_usd(scene, _JUDGE_PURPOSES) if judged else 0
    writer = _each_call_usd(scene, _WRITER_PURPOSES)
    about = video + judge
    if from_note:
        about += writer
    remakes = auto_fix.MOST_REMAKES * (video + judge + writer) if judged else Decimal(0)
    return Estimate(about, about + remakes)


def _each_call_usd(scene: Scene, purposes: list[str]) -> Decimal:
    """What one call for one of `purposes` is estimated to cost: the average of those made
    for the scene's last clip, or else of those made for the ad, or the fallback when none
    is recorded."""
    paid = ModelCall.objects.filter(job=scene.job, purpose__in=purposes, cost_usd__gt=0)
    last = scene.steps.filter(kind=SceneStep.Kind.CLIP, status=SceneStep.Status.FINISHED).last()
    for calls in (paid.filter(tool_call=last.tool_call) if last else paid.none(), paid):
        average: Decimal | None = calls.aggregate(cost=Avg("cost_usd"))["cost"]
        if average:
            return average
    return CALL_FALLBACK_USD


def _before_its_clip(scene: Scene) -> None:
    """Refuse to change a scene that already has a clip: from then on it changes only through
    the shop owner's notes, which the app plans, shows them and makes once they say yes (fix
    redesign, decisions 1 and 2). Checked once nothing already made can be handed back, so
    what is free and changes nothing stays free."""
    if scene.produced.filter(kind=ProducedItem.Kind.CLIP).exists():
        raise Refused(
            f"scene {scene.number} already has a clip, so it changes only through the shop "
            "owner's notes: the app shows them a plan and makes it once they say yes."
        )


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
) -> None:
    """Start a scene step in the background, from the scene's line, what it shows and its
    B-roll labels as they stand, unless one of its kind is already running for the scene:
    then refuse, saying `busy`."""
    if scene.steps.filter(kind=kind, status=SceneStep.Status.RUNNING).exists():
        raise Refused(busy)
    try:
        with transaction.atomic():
            step = SceneStep.objects.create(
                scene=scene,
                kind=kind,
                tool_call=call,
                **SceneStep.made_for(scene),
                note=note,
                made_from=made_from,
                picture=picture,
                picture_step=picture_step,
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
    and the job has a product photo, from the page or from the shop owner."""
    return _page_checked(job) and job.photos.exists()


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
            f"The product's colour: {job.product_colour}, shown in photos "
            f"{', '.join(str(number) for number in colour_photos)}."
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
    """Show the shop owner the job's photos in the chat, as pictures on the agent's message."""
    messages.add(
        call.session,
        role=Message.Role.AGENT,
        carrying=[
            messages.AttachedFile(Attachment.Kind.PICTURE, photo.file) for photo in job.photos.all()
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
        RunPlanningChecks,
        CreateMusic,
        MakeStartingPicture,
        MakeLineAudio,
        TranscribeLineAudio,
        MakeClip,
        AssembleAd,
    ],
    # What code does with the fix plans it runs reaches the producer only so (decision 20).
    reads_system_notes=True,
)
