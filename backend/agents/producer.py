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
from jobs import page
from jobs.models import Job, ProducedItem, ProductPhoto, Scene, SceneStep
from jobs.work import (
    check_page,
    create_person,
    keep_page,
    latest,
    plan,
    run_checks,
    save_photos,
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
checks, then make each scene's starting picture. When a tool hands back something to ask \
the shop owner, ask it in your reply and wait for their answer before passing their choice \
to a tool.
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
                scene.line = " ".join(line_choice.own_line.split())
            # The shop owner knows their product: the line they chose isn't checked again.
            scene.fact_checked = True
            scene.save(update_fields=["line", "fact_checked"])
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
        running = f"scene {scene.number}'s starting picture is already being made"
        if steps.filter(status=SceneStep.Status.RUNNING).exists():
            raise Refused(f"{running}. You'll be told when it's ready.")
        try:
            with transaction.atomic():
                step = SceneStep.objects.create(
                    scene=scene,
                    kind=SceneStep.Kind.STARTING_PICTURE,
                    tool_call=call,
                    line=scene.line,
                    note=note,
                )
        except IntegrityError:
            # Another started it since the look above.
            raise Refused(f"{running}. You'll be told when it's ready.") from None
        # tasks.py runs the producer, so it imports this module rather than the other way.
        from .tasks import run_scene_step

        transaction.on_commit(lambda: run_scene_step.delay(step.pk))
        return (
            f"Started scene {scene.number}'s starting picture. It isn't made yet: you'll be "
            "told when it's ready."
        )


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
    tools=[ReadPage, UsePhotos, PlanAd, CreatePerson, RunPlanningChecks, MakeStartingPicture],
)
