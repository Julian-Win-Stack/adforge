"""The producer: the agent the user talks to. What it is told to do, and its tools."""

import re
from decimal import Decimal
from typing import Literal, NamedTuple

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.db import IntegrityError, transaction
from django.db.models import Avg, Max, Sum
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from adforge.retry import OutsideServiceDown
from chat import messages
from chat.models import Attachment, Message, Session
from gateway import catalog
from gateway.models import ModelCall
from gateway.types import MusicHandoff, UnusableReply
from jobs import auto_fix, clip_judge, page
from jobs.checks import LONGEST_BROLL_LINE_SECONDS, LONGEST_LINE_SECONDS, line_seconds
from jobs.models import (
    BROLL_FIELDS,
    Fix,
    FixCheck,
    Job,
    ProducedItem,
    ProductPhoto,
    Scene,
    SceneStep,
)
from jobs.notices import post_notice
from jobs.work import (
    DECLARED_INSTEAD,
    SkippedPhoto,
    assemble_ad,
    bring_back_clip,
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
    why_the_checks_havent_passed,
    why_the_checks_passed,
)

from . import step_signals
from .loop import Agent, Refused, Tool
from .models import ToolCall
from .money import dollars

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
When a tool skips a product photo, tell the shop owner which photo and why. When a tool \
fails or refuses, tell the shop owner honestly what happened.
Once a scene has a clip, changing it is a fix. When the shop owner writes about such a \
scene, pick the smallest thing to make again that fixes it: how a scene that shows the \
product moves, or what happens in it, is a new clip; what a scene's picture shows, such as \
the wrong side, an object that couldn't be real or the wrong thing on screen, is a new \
starting picture, then its clip; what a scene says is a new line, with the new line in the \
fix word for word, in quotes. "Zoom in" means a new starting picture, closer: the camera \
never moves.
Before you reply, call restate_fix once, with a fix for every scene they wrote about that \
you can fix. Then tell them in their own words what you'll change in each scene, what it \
will cost, and anything you can't do, and wait for their answer. If a scene they wrote \
about is still being made, tell them its fix waits until that's finished. Never tell \
them which step is made again. If they correct you, restate it again. Make nothing until \
they say yes.
When they say yes, make every scene's fix at the same time. For a new clip, pass its fix \
word for word as the note of make_clip; for a new starting picture, as the note of \
make_starting_picture, then make_clip with no note once the picture is ready; for a new \
line, call change_line with the new line exactly as it is in the fix, then make the scene \
again in the usual order, with no note. When you're told something of a scene is ready and \
a fix they said yes to for it isn't made yet, make it then.
When a fix's clip is ready and every scene is finished, the ad is assembled and shown \
to them on its own. Tell them what the fix cost, and ask them to tell you if it's \
fixed. Never say it is: you can't see pictures or videos. If they say it still isn't \
right, restate a new fix for that scene the same way, making the same thing again.
A new starting picture or clip made for a fix is checked for the change. When the check \
finds it missing, the chat shows them the old one and the new one and why: make nothing \
more for that scene, and don't assemble the ad, until they answer. If they say go ahead, \
carry on; if they say what to change, restate a new fix.
What you can't do, said plainly the same way each time: see pictures or videos; use new \
product photos once the ad is planned; give the video model a photo, as it only starts from \
the starting picture; or change what the person does in a scene where they talk, only how \
it looks or what they hold, through a new starting picture. Not yet: changing the music, \
the text shown over a scene, the voice, the person, the number of scenes or the ad's \
length. Slower can't be done: every clip plays at real-time speed.
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
            return (
                f"The page couldn't be read: {error} Ask the shop owner for a working link to "
                "the product's own page."
            )
        download = read.download
        product_page = page.parse(download)
        check = check_page(job, download, product_page)
        if check.decision == "unreadable":
            return (
                f"The page was read but can't be used: {check.reason} Ask the shop owner for "
                "a link to the product's own page."
            )
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
        if picked.urls is None:
            skipped = save_photos(job, product_page.photo_urls)
        else:
            skipped = save_photos(job, picked.urls, merge_copies=True, faces=picked.faces)
            if skipped and not job.photos.exclude(source_url="").exists():
                post_notice(job, _none_kept(skipped), Message.Level.PROBLEM)
                skipped = save_photos(job, product_page.photo_urls)
            elif skipped:
                post_notice(job, _not_downloaded(skipped), Message.Level.PROBLEM)
        # Only once every photo is kept: a worker that stops part-way through them leaves the
        # page unread, so the read run again keeps them all rather than the few already kept.
        keep_page(job, download, product_page, text)
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
        return "\n".join(told)


class UsePhotos(Tool):
    """Add the product photos the shop owner attached to their messages in this chat to the
    job, after the page's own. Every photo they sent is added, so to leave one out, ask them
    which to send. Use it when the page had no usable photos or they want their own used."""

    name = "use_photos"

    def run(self, call: ToolCall) -> str:
        job = _with_its_page(call, "the photos belong to its ad")
        if job.scenes.exists():
            raise Refused(
                "the ad is already planned, so its photos can't change now. Changing a planned "
                "ad's photos comes later."
            )
        kept = set(job.photos.values_list("file", flat=True))
        position = job.photos.aggregate(last=Max("position"))["last"] or 0
        added = 0
        for attached in Attachment.objects.filter(
            message__session=call.session,
            message__role=Message.Role.USER,
            kind=Attachment.Kind.PICTURE,
        ).order_by("message__seq", "position"):
            if attached.file in kept:
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
        return f"Added {added} of the shop owner's photos. The job now has {_photos(count)}."


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
        "asked for this scene, as a short instruction. For a scene that already has a clip: "
        "its fix, word for word, once the shop owner has said yes. Null for nothing."
    )

    def run(self, call: ToolCall) -> str:
        scene = _a_checked_scene(call, self.scene)
        if latest(scene.job, ProducedItem.Kind.PORTRAIT) is None:
            raise Refused(
                "the person hasn't been made yet, and the picture shows them. Create the "
                "person first."
            )
        note = normalised_note(self.note)
        fix = _confirmed_fix(call, scene, note, Fix.Redo.PICTURE)
        if not note:
            fix = _line_fix(scene)
        steps = scene.steps.filter(kind=SceneStep.Kind.STARTING_PICTURE)
        made = (
            steps.filter(status=SceneStep.Status.FINISHED, note=note, **SceneStep.made_for(scene))
            .filter(produced__isnull=False)
            .last()
        )
        if made is not None:
            picture = made.produced.get()
            assert made.photo is not None, "a finished starting picture was made from a photo"
            return (
                f"Scene {scene.number}'s starting picture was already made for this line "
                f"{'with this note' if note else 'with no note'} (version {picture.version}), "
                "and the shop owner has seen it, so nothing was made or paid for again. Making "
                f"it cost {dollars(made.tool_call.cost_usd())}. Photo {made.photo.position} "
                f"was used: {made.photo_reason}"
            )
        # A clip being made of the scene's picture would finish the scene once a newer
        # picture had un-finished it (#133).
        if scene.steps.filter(kind=SceneStep.Kind.CLIP, status=SceneStep.Status.RUNNING).exists():
            raise Refused(
                f"scene {scene.number}'s clip is still being made. You'll be told when it's "
                "ready; make its starting picture again then."
            )
        _a_fix_unless_first_making(scene, fix)
        _start_step(
            call,
            scene,
            SceneStep.Kind.STARTING_PICTURE,
            busy=f"scene {scene.number}'s starting picture is already being made. You'll be "
            "told when it's ready.",
            note=note,
            fix=fix,
        )
        return (
            f"Started scene {scene.number}'s starting picture. It isn't made yet: you'll be "
            "told when it's ready."
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
            fix=_line_fix(scene),
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
            fix=_line_fix(scene),
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
        "audio stay as they are, and only the clip is made again. For a scene that already "
        "has a clip: its fix, word for word, once the shop owner has said yes. Null for "
        "nothing, as for the clip of a fix's new starting picture.",
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
        fix = _confirmed_fix(call, scene, note, Fix.Redo.CLIP)
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
        # A line shortened to fit its clip still shows what its picture was made for.
        made_for = SceneStep.made_for(scene)
        del made_for["line"]
        picture_step = picture_steps.filter(
            line__in=[scene.line, *scene.shortened_from], **made_for
        ).last()
        if picture_step is None:
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
        # A clip of a picture made for a fix is part of that fix: the shop owner's yes to it
        # covered the clip too, so it needs no note of its own.
        if not note and picture_step.fix_id is not None and picture_step.fix == scene.fixes.last():
            fix = picture_step.fix
            _answered_a_failed_check(call, picture_step)
        picture = picture_step.produced.first()
        made = (
            scene.produced.filter(
                kind=ProducedItem.Kind.CLIP,
                step__status=SceneStep.Status.FINISHED,
                made_from=audio,
                # A clip made before clips kept their picture step has only its picture.
                **({"picture": picture} if picture else {"step__picture_step": picture_step}),
                # With no note, any clip of the same picture and audio is already made, as
                # before notes; a different note is a new clip.
                **({"step__note": note} if note else {}),
            )
            .order_by("version")
            .last()
        )
        if made is not None:
            assert made.step is not None, "a clip is made by a scene step"
            made_from = "from this starting picture" if picture else "for this scene"
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
                again = bring_back_clip(made)
                back = f" It is the scene's clip again, as version {again.version}."
            return (
                f"Scene {scene.number}'s clip was already made {made_from} and audio"
                f"{with_note} (version {made.version}), so nothing was made or paid for "
                f"again.{back} Making it cost {dollars(made.step.tool_call.cost_usd())}. "
                f"Scene {scene.number} is finished."
            )
        _a_fix_unless_first_making(scene, fix)
        _start_step(
            call,
            scene,
            SceneStep.Kind.CLIP,
            busy=f"scene {scene.number}'s clip is already being made. You'll be told when it's "
            "ready.",
            note=note,
            fix=fix,
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


def assemble_the_ad(session: Session, job: Job) -> tuple[ProducedItem, bool]:
    """Assemble the finished ad from every scene's clip and the music, and show it in the
    chat, unless it was already assembled from them: then it is shown if it isn't yet, and
    nothing is made again. Gives the ad, and whether it was already assembled. Refuses,
    saying why, until every scene is finished and the music is made. Costs nothing."""
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
            _show(session, ad)
            return ad, True
    ad = assemble_ad(job, scene_clips, music)
    _show(session, ad)
    return ad, False


def assemble_after_fix(step: SceneStep) -> str:
    """For a clip `assembles_after` that has just landed, its step marked finished: assemble
    the new ad and show it once every scene is finished, so a producer that forgets can't
    leave the shop owner without it (Julian, 2026-10-11 22:24, on #135). Says what was done,
    for the producer: the ad assembled, what it waits for, or why it couldn't be. Costs nothing."""
    job = step.scene.job
    # Two fixes' clips landing at once: the second waits here for the first's transaction,
    # then sees that clip landed, so the last to land assembles.
    Job.objects.select_for_update().get(pk=job.pk)
    # A scene with any step running is being made or fixed.
    unfinished = [
        scene
        for scene in job.scenes.all()
        if scene.status != Scene.Status.FINISHED
        or scene.steps.filter(status=SceneStep.Status.RUNNING).exists()
    ]
    # A finished scene whose latest fix the shop owner has answered but whose clip isn't made
    # yet, as when its new starting picture has landed but its clip isn't started, or when
    # this clip is an older fix's and they said yes to a newer one while it was being made:
    # the ad they already corrected isn't shown (#140 review, must-fix 1).
    unfixed = [
        scene for scene in job.scenes.all() if scene not in unfinished and _fix_still_to_land(scene)
    ]
    # A scene whose fix's clip failed its check waits for the shop owner's word: no ad is
    # shown with it until they answer (#131), whichever scene's clip lands last.
    session = step.tool_call.session
    unchecked = [
        scene
        for scene in job.scenes.all()
        if scene not in unfinished
        and scene not in unfixed
        and _waits_on_a_failed_check(session, _latest_clip(scene))
    ]
    again = " again" if job.produced.filter(kind=ProducedItem.Kind.FINISHED_AD).exists() else ""
    if unfinished or unfixed or unchecked:
        waits = []
        if unfinished:
            is_ = "is" if len(unfinished) == 1 else "are"
            waits.append(f"{_scenes([scene.number for scene in unfinished])} {is_} finished")
        for scene in unfixed:
            newer = " newer" if scene == step.scene else ""
            waits.append(f"scene {scene.number}'s{newer} fix is made")
        for scene in unchecked:
            waits.append(f"the shop owner answers scene {scene.number}'s failed fix check")
        # An answer no redo has followed yet may have been no, which code can't tell from a yes.
        unmade = [scene for scene in unfixed if scene.fixes.last().confirmed_by_id is None]  # type: ignore[union-attr]
        no = (
            f" If the shop owner said no to {_scenes([scene.number for scene in unmade])}'s "
            "fix, assemble the ad with assemble_ad instead."
            if unmade
            else ""
        )
        return (
            f"The ad will be assembled{again}, and shown to the shop owner, once "
            f"{_listed(waits)}.{no}"
        )
    try:
        ad, _ = assemble_the_ad(step.tool_call.session, job)
    except Refused as refused:
        return f"The ad couldn't be assembled{again}: {refused}"
    return (
        f"Every scene is finished, so the ad was assembled{again} with it (version {ad.version}, "
        f"{ad.seconds:g} seconds) and shown to the shop owner in the chat. Ask them to tell you "
        "if it's fixed."
    )


def assembles_after(step: SceneStep) -> bool:
    """Whether code assembles the ad once this step has finished: it is a fix's clip, or the
    clip of a scene still being made for the first time while a scene the shop owner has
    already said yes to fixing waits for the ad. A scene can be fixed before the whole ad is
    first made (#135 question 5), and its clip was told the ad would be assembled once the
    others are finished, so the last of their clips keeps that promise."""
    if step.kind != SceneStep.Kind.CLIP:
        return False
    if step.fix_id is not None:
        return True
    job = step.scene.job
    return (
        job.fixes.filter(confirmed_by__isnull=False).exists()
        and not job.produced.filter(kind=ProducedItem.Kind.FINISHED_AD).exists()
    )


def _line_fix(scene: Scene) -> Fix | None:
    """The scene's fix for a new line, once the shop owner has said yes to it and the line is
    changed: the scene is made again for it with no note, so each step is part of the fix and
    paid for under it. Once its clip has landed, each is already made and handed back free. A
    fix is confirmed only as change_line changes the line, so a confirmed one has changed it."""
    fix = scene.fixes.last()
    if fix is None or fix.redo != Fix.Redo.LINE or fix.confirmed_by_id is None:
        return None
    return fix


def _fix_still_to_land(scene: Scene) -> bool:
    """Whether the shop owner has answered the scene's latest fix but its clip isn't the
    scene's clip yet. Answered, not only confirmed by a redo: the producer waits for a
    scene's clip still being made before making its newer fix (`_waits_for`), and the clip
    landing meanwhile mustn't show an ad the owner already corrected (#140 review, must-fix
    1). A clip brought back for it, made earlier with the same fix as its note, counts."""
    fix = scene.fixes.last()
    if fix is None or not _owner_answered(fix):
        return False
    clip = _latest_clip(scene)
    step = clip.step if clip is not None else None
    return step is None or (step.fix_id != fix.pk and step.note != fix.fix)


class FixAsked(BaseModel):
    """One scene's fix, as the producer restates it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    scene: int = Field(description="The number of the scene.")
    fix: str = Field(
        description="What you will change about the scene, restated in one self-contained line "
        "that carries everything the shop owner asked for it, in every note on it. Once they "
        "say yes, it is passed word for word as the note of the redo."
    )
    redo: Literal["clip", "picture", "line"] = Field(
        description='What is made again: "clip" to change only how a scene that shows the '
        'product moves, from its starting picture as it is; "picture" to change what the '
        'scene shows, with a new starting picture and then its clip; "line" to change what is '
        "said, with the new line in the fix word for word, in quotes. The shop owner isn't "
        "told which."
    )


class RestateFix(Tool):
    """Record what you will change about scenes that have their clip, from the shop owner's
    notes on them, before anything is made again, and work out what each fix will cost. A
    scene that already has a clip is only made again for a fix restated here, once the shop
    owner has answered: tell them your restatement and the cost, and wait for their answer.
    If they correct it, restate it again: the new fix replaces the old one."""

    name = "restate_fix"

    fixes: list[FixAsked] = Field(description="One fix for each scene the notes are about.")

    def run(self, call: ToolCall) -> str:
        job = _the_job(call)
        # A scene can be fixed as soon as it has a clip, while the others are still being
        # made (Julian 2026-10-12 00:26, #135 review question 5).
        if job is None or not job.scenes.exists():
            raise Refused("the ad hasn't been planned yet, and a fix changes one of its scenes.")
        if not self.fixes:
            raise Refused("no fix was given. Restate at least one scene's fix.")
        numbers = [asked.scene for asked in self.fixes]
        twice = next((number for number in numbers if numbers.count(number) > 1), None)
        if twice is not None:
            raise Refused(
                f"scene {twice} is restated twice. Give each scene one fix that carries "
                "everything the shop owner asked for it."
            )
        # Every fix is checked before any is recorded, so a refusal records nothing.
        fixable = [(_fixable_scene(job, asked), asked) for asked in self.fixes]
        estimates = {
            scene.pk: _estimate(scene, Fix.Redo(asked.redo), normalised_note(asked.fix))
            for scene, asked in fixable
        }
        recorded = []
        with transaction.atomic():
            for scene, asked in fixable:
                redo = Fix.Redo(asked.redo)
                # Run again after a restart, the call records the same fixes, not more.
                fix, _ = Fix.objects.update_or_create(
                    asked=call,
                    scene=scene,
                    defaults={
                        "job": job,
                        "fix": normalised_note(asked.fix),
                        "redo": redo,
                        "notes": fix_notes(scene),
                        "estimate_usd": estimates[scene.pk].about,
                    },
                )
                recorded.append(fix)
        call.asked_about = "fixes"
        each = " ".join(
            f"Scene {fix.scene.number}: {_about(estimates[fix.scene.pk])}." for fix in recorded
        )
        about = sum((estimates[fix.scene.pk].about for fix in recorded), Decimal(0))
        up_to = sum((estimates[fix.scene.pk].up_to for fix in recorded), Decimal(0))
        total = f"About {dollars(about)} in all" + (
            f", up to {dollars(up_to)} if the judge has a clip made again" if up_to != about else ""
        )
        waits = "".join(_waits_for(fix.scene) + _earlier_fix_lost(fix) for fix in recorded)
        return (
            f"Recorded the fix{'es' if len(recorded) != 1 else ''}. {each} {total}. "
            "Tell the shop owner this restatement and what it will "
            "cost, and wait for their answer: nothing is made until they say yes. Once they "
            "have, pass each scene's fix word for word as the note of its redo, or, for a new "
            f"line, the new line to change_line.{waits}"
        )


def _about(estimate: Estimate) -> str:
    """An estimate as the shop owner is told it: about what it will cost, and, when the clip
    judge can have a clip made again, the most it can cost."""
    about = f"about {dollars(estimate.about)}"
    if estimate.up_to == estimate.about:
        return about
    return f"{about}, up to {dollars(estimate.up_to)} if the judge has it made again"


# What the producer calls each kind of step, in what it is told.
STEP_NAMES = {
    SceneStep.Kind.STARTING_PICTURE: "starting picture",
    SceneStep.Kind.LINE_AUDIO: "line's audio",
    SceneStep.Kind.TRANSCRIPT: "transcript",
    SceneStep.Kind.CLIP: "clip",
}


def _earlier_fix_lost(fix: Fix) -> str:
    """For a fix for a new line on a scene whose last fix the shop owner said yes to changed its
    picture or clip: that the new line makes them again from scratch, with no note, so the
    earlier change may not stay, and the shop owner is told before their answer (#138
    review). Empty otherwise."""
    if fix.redo != Fix.Redo.LINE:
        return ""
    earlier = fix.scene.fixes.exclude(pk=fix.pk).filter(confirmed_by__isnull=False).last()
    if earlier is None or earlier.redo == Fix.Redo.LINE:
        return ""
    return (
        f" Scene {fix.scene.number} had an earlier fix the shop owner said yes to "
        f'("{earlier.fix}"), and a new line makes its picture and clip again from scratch, so '
        "that change may not stay: tell the shop owner that too, before their answer."
    )


def _waits_for(scene: Scene) -> str:
    """For a fix restated while something of its scene is still being made, as when the shop
    owner sends a second note while the first fix's clip is being made: that its redo waits
    for it, since a redo then is refused as busy, so the producer tells the shop owner (#137
    review). Empty when nothing of the scene is being made."""
    running = [
        STEP_NAMES[SceneStep.Kind(kind)]
        for kind in scene.steps.filter(status=SceneStep.Status.RUNNING)
        .order_by("started_at", "id")
        .values_list("kind", flat=True)
    ]
    if not running:
        return ""
    is_ = "is" if len(running) == 1 else "are"
    return (
        f" Scene {scene.number}'s {' and '.join(running)} {is_} still being made, so its fix "
        "can only be made once that's ready: tell the shop owner that too, and make the fix "
        "when you're told it is."
    )


class ChangeLine(Tool):
    """Give a scene that has its clip a new line, for its fix restated as a new line once the
    shop owner has answered: the new line exactly as it is in the fix. Its starting picture,
    audio, transcript and clip were made for the old line, so they are then made again, with
    no note, and its clip assembles the new ad. Costs nothing itself."""

    name = "change_line"

    scene: int = Field(description="The number of the scene.")
    line: str = Field(description="The new line, word for word as it is in the scene's fix.")

    def run(self, call: ToolCall) -> str:
        scene = _a_checked_scene(call, self.scene)
        number = scene.number
        line = normalised_note(self.line)
        fix = scene.fixes.last()
        if fix is None or fix.redo != Fix.Redo.LINE:
            raise Refused(
                f"scene {number}'s line changes only for a fix restated as a new line, and "
                + (
                    "no fix was restated for it."
                    if fix is None
                    else f"its fix was restated as a new {fix.get_redo_display().lower()}."
                )
                + " Call restate_fix first and wait for the shop owner's answer."
            )
        if line != quoted_line(fix.fix):
            raise Refused(
                f"the line isn't the new line in quotes in scene {number}'s fix as it was "
                f'restated to the shop owner: "{fix.fix}" Pass the words inside the quotes '
                "exactly, or restate the fix."
            )
        # Changed already, as when run again after a restart, maybe shortened since to fit
        # its clip.
        if line == scene.line or (fix.confirmed_by is not None and line in scene.shortened_from):
            return (
                f'Scene {number}\'s line is already "{scene.line}", so nothing was changed. '
                + _MAKE_IT_AGAIN
            )
        if line in scene.shortened_from:
            raise Refused(
                f"scene {number}'s line was shortened from this line because its audio was too "
                "long for its clip, so it can't be used as it stands. Ask the shop owner for a "
                "shorter line."
            )
        if scene.steps.filter(status=SceneStep.Status.RUNNING).exists():
            raise Refused(
                f"scene {number} is still being made. You'll be told when it's ready; change "
                "its line then."
            )
        with transaction.atomic():
            if fix.confirmed_by is None:
                fix.confirmed_by = _yes_to(call.session, fix)
                fix.save(update_fields=["confirmed_by"])
            # The shop owner read the line and said yes: they know their product, so it isn't
            # fact checked, and the scene stays checked (Julian, 2026-10-11 22:13, #138).
            scene.change_line(line)
            scene.save(update_fields=["line", "shortened_from", "status"])
        return f'Scene {number}\'s line is now "{line}". ' + _MAKE_IT_AGAIN


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


_MAKE_IT_AGAIN = (
    "Its starting picture, audio, transcript and clip were made for the old line, so make "
    "them again with no note: its starting picture and its line's audio, then transcribe the "
    "audio, then make its clip. The ad is assembled again when its clip is ready."
)


def _fixable_scene(job: Job, asked: FixAsked) -> Scene:
    """The scene a fix is restated for, once it can be fixed that way: it exists, has a clip
    to fix, and its redo can be made. Refuses, saying why, otherwise."""
    scene = job.scenes.filter(number=asked.scene).first()
    if scene is None:
        raise Refused(
            f"the ad has no scene {asked.scene}: its scenes are 1 to {job.scenes.count()}."
        )
    if not scene.produced.filter(kind=ProducedItem.Kind.CLIP).exists():
        raise Refused(
            f"scene {scene.number} has no clip yet, so it isn't finished and there is nothing "
            "to fix."
        )
    # A clip is kept before its step finishes, so a scene's first clip can be saved while
    # its step is still running, or one that then failed.
    if _latest_clip(scene) is None:
        if scene.steps.filter(kind=SceneStep.Kind.CLIP, status=SceneStep.Status.RUNNING).exists():
            # Decider call 14, 2026-10-13.
            raise Refused(
                f"scene {scene.number}'s clip is still being made, so there is nothing to fix "
                "yet. Restate its fix when you're told it's ready, and restate the other "
                "scenes' fixes now without it."
            )
        raise Refused(
            f"scene {scene.number}'s clip couldn't be finished, so there is nothing to fix. "
            "Make its clip again first."
        )
    if not normalised_note(asked.fix):
        raise Refused(f"scene {scene.number}'s fix is blank. Restate it in one line.")
    redo = Fix.Redo(asked.redo)
    if redo == Fix.Redo.CLIP:
        instead = "Restate it as a new starting picture instead."
        _refuse_unless_movement_can_change(scene, talking=instead, unlabelled=instead)
    if redo == Fix.Redo.LINE:
        line = quoted_line(normalised_note(asked.fix))
        if line is None:
            raise Refused(
                f"scene {scene.number}'s fix is a new line, but it doesn't hold exactly one line "
                "in quotes. Restate it with the new line word for word, in quotes."
            )
        # A line the shop owner confirmed is theirs, so it is never shortened for them: one
        # too long is refused before they are asked (Julian's card, 2026-10-12).
        longest = LONGEST_BROLL_LINE_SECONDS if scene.shows else LONGEST_LINE_SECONDS
        seconds = _seconds_to_say(scene, line)
        if seconds > longest:
            raise Refused(
                f"scene {scene.number}'s new line takes about {round(seconds)} seconds to say at "
                f"the voice's speed, longer than the scene can last ({longest} seconds). Ask the "
                "shop owner for a shorter line, and restate the fix with it."
            )
    return scene


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
_JUDGE_PURPOSES = ["check_clip_sides", "check_clip_motion"]
_WRITER_PURPOSES = ["rewrite_clip_prompt", "remake_clip_prompt"]


def _estimate(scene: Scene, redo: Fix.Redo, fix: str) -> Estimate:
    """What making `redo` again for the scene, for `fix`, is estimated to cost."""
    if redo == Fix.Redo.PICTURE:
        return picture_estimate(scene)
    if redo == Fix.Redo.LINE:
        return _line_estimate(scene, fix)
    return _clip_estimate(scene, _clip_seconds(scene), from_note=True)


def _line_estimate(scene: Scene, fix: str) -> Estimate:
    """What a new line for the scene, then its starting picture, audio, transcript and clip,
    is estimated to cost: the picture as for a picture fix, and the rest sized from the new
    line in `fix`, as long as the voice takes to say it."""
    line = quoted_line(fix)
    assert line is not None, "a fix for a new line holds it in quotes"
    seconds = _seconds_to_say(scene, line)
    rest = (
        _picture_only_estimate_usd(scene)
        + catalog.speech_cost_usd(catalog.MODEL_FOR_PURPOSE["speak_line"], len(line))
        + catalog.transcription_cost_usd(catalog.MODEL_FOR_PURPOSE["transcribe_line"], seconds)
    )
    clip = _clip_estimate(scene, seconds, from_note=False)
    return Estimate(rest + clip.about, rest + clip.up_to)


def picture_estimate(scene: Scene) -> Estimate:
    """What a new starting picture for the scene, then its clip, is estimated to cost."""
    picture = _picture_only_estimate_usd(scene)
    clip = _clip_estimate(scene, _clip_seconds(scene), from_note=False)
    return Estimate(picture + clip.about, picture + clip.up_to)


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


def fix_notes(scene: Scene) -> list[dict[str, object]]:
    """The shop owner's messages a fix for the scene is restated from, verbatim, as
    {"seq", "text"}: each one since the latest of the scene's first clip being made, the ad
    last being shown, and their yes to the scene's last fix. A correction to a fix they
    haven't confirmed keeps the notes before it, so the new fix holds every note it
    carries."""
    session = scene.job.session
    assert session is not None, "a fix is restated in the job's chat"
    ads = scene.job.produced.filter(kind=ProducedItem.Kind.FINISHED_AD).values_list(
        "file", flat=True
    )
    shown: int | None = session.messages.filter(attachments__file__in=ads).aggregate(
        seq=Max("seq")
    )["seq"]
    confirmed: int | None = scene.fixes.aggregate(seq=Max("confirmed_by__seq"))["seq"]
    first_clip = scene.produced.filter(kind=ProducedItem.Kind.CLIP).order_by("created_at").first()
    assert first_clip is not None, "a fix is for a scene with a clip"
    said = session.messages.filter(
        role=Message.Role.USER,
        seq__gt=max(shown or 0, confirmed or 0),
        created_at__gt=first_clip.created_at,
    ).order_by("seq")
    return [{"seq": seq, "text": text} for seq, text in said.values_list("seq", "text")]


def _a_fix_unless_first_making(scene: Scene, fix: Fix | None) -> None:
    """Refuse to pay for a new picture or clip of a scene that already has a clip unless it is
    made for a confirmed fix: with no note, too, nothing is paid before the shop owner's
    yes (#135 review, must-fix 1). Checked once nothing already made can be handed back, so
    what is free stays free."""
    if fix is None and scene.produced.filter(kind=ProducedItem.Kind.CLIP).exists():
        raise Refused(
            f"scene {scene.number} already has a clip, so changing it is a fix. Call "
            "restate_fix first, wait for the shop owner's answer, then pass the fix word for "
            "word as the note."
        )


def _confirmed_fix(call: ToolCall, scene: Scene, note: str, redo: Fix.Redo) -> Fix | None:
    """The yes guard (#135): the fix a redo with `note` is made for, once the shop owner has
    answered its restatement, their first answer kept as their yes. Refuses, saying why,
    when the scene's latest fix isn't `note` word for word or is for the other redo, or they
    haven't answered since it was restated, so nothing is paid for before their yes. None
    for no note, or for a scene with no clip yet: making the ad the first time, from what
    the shop owner said, isn't fixing it."""
    if not note or not scene.produced.filter(kind=ProducedItem.Kind.CLIP).exists():
        return None
    fix = scene.fixes.last()
    if fix is None:
        raise Refused(
            f"scene {scene.number} already has a clip, so changing it is a fix, and no fix was "
            "restated for it. Call restate_fix first and wait for the shop owner's answer."
        )
    if fix.fix != note:
        raise Refused(
            f"the note isn't scene {scene.number}'s fix as it was restated to the shop owner: "
            f'"{fix.fix}" Pass that fix word for word, or restate it.'
        )
    if fix.redo != redo:
        raise Refused(
            f"scene {scene.number}'s fix was restated as a new starting picture, not a new "
            "clip. Make its starting picture with the fix as the note, then its clip with no "
            "note."
            if fix.redo == Fix.Redo.PICTURE
            else f"scene {scene.number}'s fix was restated as a new line. Change its line with "
            "change_line, then make the scene again with no note."
            if fix.redo == Fix.Redo.LINE
            else f"scene {scene.number}'s fix was restated as a new clip, not a new starting "
            "picture. Make its clip with the fix as the note."
        )
    if fix.confirmed_by is None:
        fix.confirmed_by = _yes_to(call.session, fix)
        fix.save(update_fields=["confirmed_by"])
    return fix


def _answered_a_failed_check(call: ToolCall, picture_step: SceneStep) -> None:
    """Refuses a clip of a fix's new starting picture whose check found the change missing
    until the shop owner has answered since they were shown it (#131): its clip is paid for
    only on their word. Code can't tell their go ahead from a new note, so any answer will
    do: a new note is restated as a new fix, which this clip's picture isn't made for."""
    picture = picture_step.produced.first()
    if _waits_on_a_failed_check(call.session, picture):
        raise Refused(
            f"scene {picture_step.scene.number}'s new starting picture failed the check of its "
            "fix, and the shop owner hasn't answered since they were shown it. Wait for their "
            "answer."
        )


def _waits_on_a_failed_check(session: Session, made: ProducedItem | None) -> bool:
    """Whether `made` failed the check of its fix and the shop owner hasn't answered since
    they were shown it: until its notice is posted they haven't been, whatever they wrote."""
    if made is None:
        return False
    check = FixCheck.objects.filter(new=made, verdict=FixCheck.Verdict.FAILED).first()
    if check is None:
        return False
    return (
        check.notice is None
        or not session.messages.filter(role=Message.Role.USER, seq__gt=check.notice.seq).exists()
    )


def _owner_answered(fix: Fix) -> bool:
    """Whether the shop owner has answered the fix's restatement: a redo confirmed it, or
    there is an answer `_yes_to` would take as their yes. If their answer was no, the
    producer's assemble_ad still assembles the ad."""
    if fix.confirmed_by_id is not None:
        return True
    if fix.asked.finished_at is None:
        return False
    try:
        _yes_to(fix.asked.session, fix)
    except Refused:
        return False
    return True


def _yes_to(session: Session, fix: Fix) -> Message:
    """The shop owner's answer to the fix's restatement: their first message after the
    producer's first words since restate_fix recorded it, which are the restatement and its
    cost. A message they sent before seeing it isn't their answer (#135 review, should-fix
    2). Refuses, saying why, when there is none."""
    number = fix.scene.number
    restated = (
        session.messages.filter(role=Message.Role.AGENT, created_at__gt=fix.asked.finished_at)
        .exclude(text="")
        .order_by("seq")
        .first()
    )
    if restated is None:
        raise Refused(
            f"scene {number}'s fix hasn't been restated to the shop owner yet. Tell them the "
            "restatement and its cost, and wait for their answer."
        )
    answer = (
        session.messages.filter(role=Message.Role.USER, seq__gt=restated.seq)
        .order_by("seq")
        .first()
    )
    if answer is None:
        raise Refused(
            f"the shop owner hasn't answered since scene {number}'s fix was restated to them. "
            "Wait for their answer."
        )
    return answer


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


def _show(session: Session, ad: ProducedItem) -> None:
    """Show the finished ad in the chat, unless it already is, as after a restart."""
    if not Attachment.objects.filter(message__session=session, file=ad.file).exists():
        messages.add(
            session,
            role=Message.Role.AGENT,
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
    fix: Fix | None = None,
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
                fix=fix,
                made_from=made_from,
                picture=picture,
                picture_step=picture_step,
            )
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
        RestateFix,
        ChangeLine,
    ],
)
