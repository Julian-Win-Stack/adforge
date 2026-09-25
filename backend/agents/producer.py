"""The producer: the agent the user talks to. What it is told to do, and its tools."""

from decimal import Decimal
from typing import Literal

from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.db import IntegrityError, transaction
from django.db.models import Max, Sum
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from chat import messages
from chat.models import Attachment, Message, Session
from gateway.models import ModelCall
from gateway.types import MusicHandoff
from jobs import page
from jobs.models import Job, ProducedItem, ProductPhoto, Scene, SceneStep
from jobs.work import (
    assemble_ad,
    check_page,
    create_music,
    create_person,
    keep_page,
    latest,
    music_mood,
    music_prompt,
    music_seconds,
    plan,
    run_checks,
    save_photos,
    use_music_again,
    why_the_checks_havent_passed,
    why_the_checks_passed,
)

from .loop import Agent, Refused, Tool
from .models import ToolCall

INSTRUCTIONS = """\
You are the producer at AdForge. You make short vertical video ads for one product at a \
time, in a chat with the shop owner who sells it. In each ad a person speaks to camera, \
one line per scene.
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
                f"again. Reading it cost {_dollars(_spent(job, self.name))}. The ad has "
                f"{_photos(job.photos.count())}."
            )
        else:
            # The page before wasn't read successfully, so nothing has been made from it: the
            # ad is the same one, from this link, and has no page until this one is read.
            job.product_url = self.link
            job.target_seconds = self.target_seconds
            job.page_text = ""
            job.page_html_key = ""
            job.status = Job.Status.READING_PAGE
            job.save(
                update_fields=[
                    "product_url",
                    "target_seconds",
                    "page_text",
                    "page_html_key",
                    "status",
                ]
            )
        try:
            download = page.download(self.link, max_bytes=page.MAX_PAGE_BYTES, what="product page")
        except page.PageUnreadable as error:
            return (
                f"The page couldn't be read: {error} Ask the shop owner for a working link to "
                "the product's own page."
            )
        product_page = page.parse(download)
        check = check_page(job, download, product_page)
        if check.decision == "unreadable":
            return (
                f"The page was read but can't be used: {check.reason} Ask the shop owner for "
                "a link to the product's own page."
            )
        skipped = save_photos(job, product_page.photo_urls)
        # Only once every photo is kept: a worker that stops part-way through them leaves the
        # page unread, so the read run again keeps them all rather than the few already kept.
        keep_page(job, download, product_page)
        kept = job.photos.count()
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
            ProductPhoto.objects.create(job=job, position=position, file=attached.file)
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
                f"Planning it cost {_dollars(_spent(job, self.name))}.\n"
                f"{_the_plan(job, planned.reason)}"
            )
        if not job.photos.exists():
            raise Refused(
                "the ad has no product photos yet, and every scene is made from one. Ask the "
                "shop owner to attach at least one, then add it with use_photos."
            )
        decision = plan(job)
        if decision.question is not None:
            return (
                f"The ad can't be planned until the shop owner answers: {decision.question} "
                f"Why: {decision.reason} Ask them, and plan again once they have answered."
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
                f"or paid for again. Making them cost {_dollars(_spent(job, self.name))}. "
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
    choice: Literal["keep", "own"] = Field(
        description='"keep" to use the line as it is, or "own" to use a line the shop owner wrote.'
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
    """Check the script before anything is made from it: every line against the product
    page, then the whole script against the target length. A line that fails is rewritten
    and checked again. Hands back what to ask the shop owner when a line still fails after
    2 rewrites, when the page itself is unclear, or when the script runs over the target.
    Once they have answered, run the checks again with their choices."""

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
        if job.target_seconds is not None and _measured_voice(job) is None:
            raise Refused(
                "the person hasn't been made yet, and the length check needs their voice's "
                "measured speed."
            )
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
            _answered(call.session, since=asked, about=about)
            if line_choice.choice == "own" and not _wrote(
                call.session, line_choice.own_line, since=asked
            ):
                raise Refused(
                    f'the shop owner never wrote "{line_choice.own_line}" in their reply to '
                    "the question. A line they give is used exactly as they wrote it, so pass "
                    "it word for word, or ask them."
                )
        if self.length_choice is not None:
            asked = self._asked(job, "length")
            if asked is None:
                raise Refused("the checks haven't asked the shop owner about the script's length.")
            _answered(call.session, since=asked, about="the script's length")
        for line_choice in self.line_choices:
            scene = job.scenes.get(number=line_choice.scene)
            if line_choice.choice == "own":
                assert line_choice.own_line is not None
                scene.change_line(" ".join(line_choice.own_line.split()))
            # The shop owner knows their product: the line they chose isn't checked again.
            scene.fact_checked = True
            scene.save(update_fields=["line", "status", "fact_checked"])
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
        ask = {
            "unclear_page": "Ask the shop owner, then run the checks again.",
            "line": "Ask the shop owner whether to keep this line or give their own.",
            "length": "Ask the shop owner whether to shorten it to fit, or keep it longer.",
        }[asking.about]
        call.asked_about = {
            "unclear_page": "the page",
            "line": f"scene {asking.scene.number if asking.scene else ''}",
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
        if voice is None:
            raise Refused(
                "the person hasn't been made yet, and the music lasts as long as their voice "
                "takes to say the script. Create the person first."
            )
        mood = music_mood(self.mood)
        prompt, seconds = music_prompt(mood), music_seconds(job, voice)
        made = job.produced.filter(kind=ProducedItem.Kind.MUSIC, text=prompt, seconds=seconds)
        music = made.order_by("version").last()
        cost = _dollars(_spent_on_music(job, prompt, seconds))
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
    """Start making a scene's starting picture: the person holding the product, made from
    the portrait and the product photo that suits the scene's line best. Works in the
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
        note = " ".join((self.note or "").split())
        steps = scene.steps.filter(kind=SceneStep.Kind.STARTING_PICTURE)
        made = (
            steps.filter(status=SceneStep.Status.FINISHED, note=note, line=scene.line)
            .exclude(produced=None)
            .last()
        )
        if made is not None:
            picture = made.produced.get()
            assert made.photo is not None, "a finished starting picture was made from a photo"
            return (
                f"Scene {scene.number}'s starting picture was already made for this line "
                f"{'with this note' if note else 'with no note'} (version {picture.version}), "
                "and the shop owner has seen it, so nothing was made or paid for again. Making "
                f"it cost {_dollars(made.tool_call.cost_usd())}. Photo {made.photo.position} "
                f"was used: {made.photo_reason}"
            )
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
                f"cost {_dollars(made.step.tool_call.cost_usd())}."
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
                f"scene {scene.number}'s audio is still being made. You'll be told when it's "
                "ready; transcribe it then."
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
                f"again. Transcribing it cost {_dollars(heard.step.tool_call.cost_usd())}. It "
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
    the same audio that was transcribed. A scene whose clip is made is finished. Works in the
    background and hands back at once; you are told when the clip is ready. Only once the
    scene's starting picture is made and its audio transcribed, for the line as it stands."""

    name = "make_clip"

    scene: int = Field(description="The number of the scene.")

    def run(self, call: ToolCall) -> str:
        scene = _a_checked_scene(call, self.scene)
        for kind, what in [
            (SceneStep.Kind.STARTING_PICTURE, "starting picture"),
            (SceneStep.Kind.LINE_AUDIO, "audio"),
            (SceneStep.Kind.TRANSCRIPT, "transcript"),
        ]:
            if scene.steps.filter(kind=kind, status=SceneStep.Status.RUNNING).exists():
                raise Refused(
                    f"scene {scene.number}'s {what} is still being made. You'll be told when "
                    "it's ready; make the clip then."
                )
        pictures = scene.produced.filter(
            kind=ProducedItem.Kind.STARTING_PICTURE, step__status=SceneStep.Status.FINISHED
        ).order_by("version")
        if not pictures.exists():
            raise Refused(
                f"scene {scene.number} has no starting picture yet. Make its starting picture "
                "first."
            )
        picture = pictures.filter(step__line=scene.line).last()
        if picture is None:
            raise Refused(
                f"scene {scene.number}'s starting picture was made for an earlier line, and the "
                "line has changed since. Make its starting picture again first."
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
        made = (
            scene.produced.filter(
                kind=ProducedItem.Kind.CLIP,
                step__status=SceneStep.Status.FINISHED,
                picture=picture,
                made_from=audio,
            )
            .order_by("version")
            .last()
        )
        if made is not None:
            assert made.step is not None, "a clip is made by a scene step"
            return (
                f"Scene {scene.number}'s clip was already made from this starting picture and "
                f"audio (version {made.version}), so nothing was made or paid for again. Making "
                f"it cost {_dollars(made.step.tool_call.cost_usd())}. Scene {scene.number} is "
                "finished."
            )
        _start_step(
            call,
            scene,
            SceneStep.Kind.CLIP,
            busy=f"scene {scene.number}'s clip is already being made. You'll be told when it's "
            "ready.",
            made_from=audio,
            picture=picture,
        )
        return (
            f"Started scene {scene.number}'s clip. It isn't made yet: you'll be told when it's "
            "ready."
        )


class AssembleAd(Tool):
    """Assemble the finished ad from every scene's clip, in order, each cut to where its
    words are said so there is no silence between scenes, and show it to the shop owner in
    the chat. Only once every scene is finished. Costs nothing."""

    name = "assemble_ad"

    def run(self, call: ToolCall) -> str:
        job = _the_job(call)
        if job is None or not job.scenes.exists():
            raise Refused("the ad hasn't been planned yet, and it is assembled from its scenes.")
        scenes = list(job.scenes.all())
        being_made = [
            scene.number
            for scene in scenes
            if scene.steps.filter(
                kind=SceneStep.Kind.CLIP, status=SceneStep.Status.RUNNING
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
        scene_clips = [_clip_and_transcript(scene) for scene in scenes]
        clips = [clip.pk for clip, _ in scene_clips]
        for ad in job.produced.filter(kind=ProducedItem.Kind.FINISHED_AD).order_by("-version"):
            if [cut["clip"] for cut in ad.cuts] == clips:
                _show(call.session, ad)
                return (
                    f"The ad was already assembled from these clips (version {ad.version}, "
                    f"{ad.seconds:g} seconds) and shown to the shop owner, so nothing was made "
                    "again. Assembling costs nothing."
                )
        ad = assemble_ad(job, scene_clips)
        _show(call.session, ad)
        first, *rest = ad.cuts
        plays = [
            f"Scene {first['scene']} plays from {first['start']:g} to {first['end']:g} seconds"
        ]
        plays += [f"scene {cut['scene']} from {cut['start']:g} to {cut['end']:g}" for cut in rest]
        return (
            f"Assembled the ad (version {ad.version}, {ad.seconds:g} seconds) from each scene's "
            "clip, cut to where its words are said, and showed it to the shop owner in the "
            f"chat. {_listed(plays)}. Tell the shop owner."
        )


def _clip_and_transcript(scene: Scene) -> tuple[ProducedItem, ProducedItem]:
    """A finished scene's clip of its line as it stands, and the transcript of the audio the
    clip speaks, whose words say where to cut it. Refuses, saying why, if no word was heard."""
    clip = (
        scene.produced.filter(
            kind=ProducedItem.Kind.CLIP,
            step__status=SceneStep.Status.FINISHED,
            step__line=scene.line,
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
) -> None:
    """Start a scene step in the background, from the scene's line as it stands, unless one
    of its kind is already running for the scene: then refuse, saying `busy`."""
    if scene.steps.filter(kind=kind, status=SceneStep.Status.RUNNING).exists():
        raise Refused(busy)
    try:
        with transaction.atomic():
            step = SceneStep.objects.create(
                scene=scene,
                kind=kind,
                tool_call=call,
                line=scene.line,
                note=note,
                made_from=made_from,
                picture=picture,
            )
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


def _answered(session: Session, *, since: ToolCall, about: str) -> None:
    """Refuse a choice the shop owner hasn't made: they haven't answered since the checks
    asked them."""
    if not session.messages.filter(
        role=Message.Role.USER, created_at__gt=since.finished_at
    ).exists():
        raise Refused(
            f"the shop owner hasn't answered since the checks asked about {about}. Ask them, "
            "and wait for their answer."
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
            f"The product's colour: {job.product_colour}, shown in photos "
            f"{', '.join(str(number) for number in colour_photos)}.",
            f"The person: {job.person_looks} Their voice: {job.person_voice}",
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


def _dollars(amount: Decimal) -> str:
    """An amount in dollars, to the cent, or to its last digit when that is smaller."""
    exact = f"{amount.normalize():f}"
    cents = f"{amount:.2f}"
    return f"${exact if len(exact) > len(cents) else cents}"


def _photos(count: int) -> str:
    return f"{count} product photo{'s' if count != 1 else ''}"


def _script(job: Job) -> str:
    return "The script:\n" + "\n".join(
        f"{scene.number}. {scene.line}" for scene in job.scenes.all()
    )


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
)
