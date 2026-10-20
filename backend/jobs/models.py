import uuid
from decimal import Decimal
from typing import Any

from django.db import models
from django.db.models import Sum
from django.utils import timezone


class Job(models.Model):
    class Status(models.TextChoices):
        QUEUED = "queued"
        READING_PAGE = "reading_page"
        PAGE_READ = "page_read"
        PLANNED = "planned"
        # The plan passed its checks, so nothing will need rewriting once rendering starts.
        READY_TO_RENDER = "ready_to_render"

    class LengthChoice(models.TextChoices):
        SHORTEN = "shorten"
        KEEP_LONGER = "keep_longer"

    class ProductSize(models.TextChoices):
        """How big the product is, which decides the pose every talking scene is planned
        around: the same values as the plan's product_size."""

        TINY = "tiny"  # fits on a fingertip: earrings, earbuds, a ring
        HANDHELD = "handheld"  # held in one or two hands: a bottle, a bag, a rolled mat
        LARGE = "large"  # can't be held: a chair, a treadmill, a mattress

    class PersonGender(models.TextChoices):
        """What the person presents as, in the portrait and the voice alike: the same values
        as the plan's person_gender."""

        MAN = "man"
        WOMAN = "woman"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    session = models.ForeignKey(
        "chat.Session",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="jobs",
        help_text="The session this job was asked for in. Blank only for jobs started "
        "before sessions existed; #15 has the agent make every job inside a session.",
    )
    variant_of = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="variants",
        help_text="The job in the same session this one varies, so the two can be compared. "
        "Variants are siblings, not versions.",
    )
    product_url = models.URLField(max_length=2000)
    target_seconds = models.PositiveSmallIntegerField(null=True, blank=True)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.QUEUED,
        help_text="A label for how far the job has got. Nothing reads it to decide what "
        "happens next: each tool decides that from what the job has.",
    )
    page_text = models.TextField(
        blank=True,
        help_text="Only this product's own text: the sentences a model copied out of the "
        "page about it, each found on the page, then the product data the page declares for "
        "search engines. Model calls read this.",
    )
    page_text_full = models.TextField(
        blank=True,
        help_text="Every word a visitor sees on the page, then its declared product data, "
        "other products' text included. Kept for debugging; no model call reads it.",
    )
    page_html_key = models.CharField(
        max_length=500,
        blank=True,
        help_text="Key in the file store of the page's original HTML, exactly as served. "
        "Blank until the page has been found to show its product and its photos are kept.",
    )
    firecrawl = models.JSONField(
        default=dict,
        blank=True,
        help_text="Firecrawl's answers for the link read, kept so reading it again reuses "
        'them: {"url": link, "files": {"page", "marked", "product", "screenshot": key in the '
        'file store}, "picker_images": [[label, key], ...]}, each added as it arrives. Empty '
        "until Firecrawl has answered.",
    )
    product_colour = models.CharField(
        max_length=100,
        blank=True,
        help_text="The colour the ad shows the product in, picked by the producer from the "
        "product photos.",
    )
    product_size = models.CharField(
        max_length=20,
        choices=ProductSize.choices,
        blank=True,
        help_text="How big the product is, from the plan. Code turns it into the pose every "
        "talking scene's starting picture and clip are asked for. Blank for a job planned "
        "before it was asked, which is posed as handheld, as every job was then.",
    )
    person_gender = models.CharField(
        max_length=10,
        choices=PersonGender.choices,
        blank=True,
        help_text="What the person presents as, from the plan. Code puts it into both the "
        "portrait's prompt and the voice's description, so the two can't disagree. Blank "
        "for a job planned before it was asked, whose person is made from the words alone.",
    )
    person_looks = models.TextField(
        blank=True, help_text="The producer's description of the person the portrait shows."
    )
    person_voice = models.TextField(
        blank=True, help_text="The producer's description of the person's voice."
    )
    script_format = models.CharField(
        max_length=30,
        blank=True,
        help_text="The structure the ad's body plays in, between its hook and its call to "
        'action, from the plan: such as "before and after" (jobs.planning.ScriptFormat). '
        "Blank for an ad planned before it was given.",
    )
    product_name = models.CharField(
        max_length=200,
        blank=True,
        help_text="The product's name as the ad says it, from the plan. At least one line says it.",
    )
    length_choice = models.CharField(
        max_length=20,
        choices=LengthChoice.choices,
        blank=True,
        help_text="What the user chose when the script didn't fit the target length.",
    )
    warnings = models.JSONField(
        default=list,
        blank=True,
        help_text="Every notice posted while making this ad, each {level, text, at}, so an "
        "ad made with a fallback can be found after the chat has scrolled away.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.product_url} ({self.status})"


class ProductPhoto(models.Model):
    job = models.ForeignKey(Job, on_delete=models.CASCADE, related_name="photos")
    position = models.PositiveSmallIntegerField()
    source_url = models.URLField(
        max_length=2000,
        blank=True,
        help_text="Where the page had it. Blank if the user uploaded it.",
    )
    file = models.CharField(max_length=500, help_text="Key in the file store.")
    shows_product_colour = models.BooleanField(
        default=False,
        help_text="Shows the product in the job's product colour, so the ad can use it.",
    )
    has_face = models.BooleanField(
        default=False,
        help_text=(
            "Shows a stranger's face you could recognise. Yes too when noting it failed: "
            "such a photo is only used when no other shows what's needed."
        ),
    )

    class Meta:
        ordering = ["job", "position"]
        constraints = [
            models.UniqueConstraint(fields=["job", "position"], name="one_photo_per_position")
        ]

    def __str__(self) -> str:
        return self.source_url


class ProductVideo(models.Model):
    """A video the shop placed with the product photos in the product's gallery, picked off
    the page with them. Kept as a file with a few frames, or, when no file can be had (an
    embedded player, a stream, a dead link, one too big), as its link with a first frame if
    one is had. Nothing uses them yet."""

    class Kind(models.TextChoices):
        VIDEO = "video", "Video"  # A video the page plays itself.
        IFRAME = "iframe", "Embedded player"  # Such as YouTube's or Vimeo's.

    job = models.ForeignKey(Job, on_delete=models.CASCADE, related_name="videos")
    position = models.PositiveSmallIntegerField()
    source_url = models.CharField(
        max_length=2000,
        blank=True,
        help_text="The player's video link on the page, which may not be a web link (blob:).",
    )
    kind = models.CharField(max_length=10, choices=Kind.choices)
    file = models.CharField(
        max_length=500, blank=True, help_text="Key in the file store; blank if no file was had."
    )
    first_frame = models.CharField(
        max_length=500,
        blank=True,
        help_text=(
            "Key of its opening picture: the file's, else the page's poster or the player's "
            "thumbnail; blank if none was had."
        ),
    )
    frames = models.JSONField(
        default=list,
        blank=True,
        help_text="Keys of frames spread over the file, first to last; none without a file.",
    )
    duration = models.FloatField(null=True, blank=True, help_text="Seconds; none without a file.")
    frames_picked = models.JSONField(
        default=list,
        blank=True,
        help_text=(
            "The file thinned to the frames worth looking at, first to last: each its key and "
            "the second it is at; none without a file."
        ),
    )

    class Meta:
        ordering = ["job", "position"]
        constraints = [
            models.UniqueConstraint(fields=["job", "position"], name="one_video_per_position")
        ]

    def __str__(self) -> str:
        return self.source_url


# What the plan says about a B-roll scene: its kind, who is in it, how the product is
# used, the result it ends on and what it needs that the main photo can't show. Blank for
# a talking scene.
BROLL_FIELDS = ["broll_kind", "person_shown", "usage", "result", "needs"]

# Each B-roll field as a scene step keeps its copy: a step's own `result` is what the
# producer is told when it finishes.
STEP_BROLL_FIELDS = {
    "broll_kind": "broll_kind",
    "person_shown": "person_shown",
    "usage": "usage",
    "result": "broll_result",
    "needs": "needs",
}


class Scene(models.Model):
    class Status(models.TextChoices):
        PLANNED = "planned"
        # Its clip is made.
        FINISHED = "finished"

    class BrollKind(models.TextChoices):
        DOES_A_JOB = "does a job"
        SHOWS_THE_PROBLEM = "shows the problem"
        SHOWCASE = "showcase"

    class PersonShown(models.TextChoices):
        NO_FACE = "no face"
        HAS_FACE = "has face"

    job = models.ForeignKey(Job, on_delete=models.CASCADE, related_name="scenes")
    number = models.PositiveSmallIntegerField(help_text="1, 2, 3... in the order they play.")
    line = models.TextField(help_text="What the person says in this scene.")
    shows = models.TextField(
        blank=True,
        help_text="What a B-roll scene shows, in plain words, while the person's voice says "
        "the line over it. Blank for a scene where the person talks to camera.",
    )
    overlay = models.TextField(
        blank=True,
        help_text="A few words drawn along the top of the picture while the scene plays, "
        "such as the price. Blank for none. Not fact checked yet.",
    )
    part = models.CharField(
        max_length=30,
        blank=True,
        help_text='The part of the script the scene plays: "hook" first, "call to action" '
        "last, and between them the parts of the job's script format, in their order. Blank "
        "for a scene planned before it was given.",
    )
    second_state = models.BooleanField(
        default=False,
        help_text="For a B-roll scene, whether it shows the second of two end states one claim "
        "needs, such as the second way a bag is carried, the first being the B-roll scene just "
        "before it.",
    )
    broll_kind = models.CharField(
        max_length=20,
        choices=BrollKind.choices,
        blank=True,
        help_text='A B-roll scene\'s kind: "does a job" (the product does something you can '
        'see), "shows the problem" (the problem it fixes, before it is used) or "showcase" '
        "(the product at its best). Blank for a talking scene, and for a B-roll scene planned "
        "before it was given.",
    )
    person_shown = models.CharField(
        max_length=20,
        choices=PersonShown.choices,
        blank=True,
        help_text="Whether a B-roll scene shows the presenter's face. Blank for a talking "
        "scene, and for a B-roll scene planned before it was given.",
    )
    usage = models.TextField(
        blank=True,
        help_text='How the product is used in a B-roll scene, from the page\'s "how to use". '
        "Blank when it isn't used, and for a talking scene.",
    )
    result = models.TextField(
        blank=True,
        help_text='What you can see at the end of a "does a job" scene, which it ends on. '
        "Blank otherwise.",
    )
    needs = models.JSONField(
        default=list,
        blank=True,
        help_text="What a B-roll scene needs that the main photo can't show, each with the "
        'numbers of the photos that show it, such as [{"what": "the gel", "photos": [3, 5]}]. '
        "Empty when the main photo is enough.",
    )
    fact_checked = models.BooleanField(
        default=False,
        help_text="The line, and what the scene shows, passed the fact check, or the user "
        "kept them.",
    )
    fact_problems = models.JSONField(
        default=list,
        blank=True,
        help_text="Why the fact check failed this line each time, oldest first. Two rewrites "
        "are tried before the user is asked.",
    )
    shortened_from = models.JSONField(
        default=list,
        blank=True,
        help_text="The lines this line was shortened from, oldest first, because their audio "
        "was too long for a B-roll clip. A starting picture made for one of them still suits "
        "it. Emptied when the line changes any other way.",
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PLANNED)

    class Meta:
        ordering = ["job", "number"]
        constraints = [
            models.UniqueConstraint(fields=["job", "number"], name="one_scene_per_number")
        ]

    def __str__(self) -> str:
        return f"Scene {self.number}: {self.line}"

    def plan_again(self) -> bool:
        """Plan the scene again if it is finished, as when it gets a new starting picture: its
        clip shows the old one, so the ad isn't assembled until a clip is made from the new
        one (#133). Saved at once. Gives whether it was finished."""
        planned = Scene.objects.filter(pk=self.pk, status=self.Status.FINISHED).update(
            status=self.Status.PLANNED
        )
        if planned:
            self.status = self.Status.PLANNED
        return bool(planned)

    def photos_in_use(self) -> models.QuerySet[ReferencePhoto]:
        """The shop owner's photos the scene uses now, which go with its later steps: those no
        fix dropped and no going back set aside."""
        return self.reference_photos.filter(dropped_by__isnull=True, set_aside_at__isnull=True)

    def change_line(
        self, line: str, shows: str | None = None, *, broll: dict[str, Any] | None = None
    ) -> None:
        """Give the scene a new line, what it shows if `shows` isn't None, and its B-roll
        labels if `broll` isn't None, unsaved. A clip says the line and shows what it was
        made for, so a finished scene is planned again: it needs a new clip. A scene that no
        longer shows anything is a talking scene, with no B-roll labels."""
        if line != self.line:
            self.line = line
            self.shortened_from = []
            self.status = self.Status.PLANNED
        if shows is not None and shows != self.shows:
            self.shows = shows
            self.status = self.Status.PLANNED
        for field, value in (broll or {}).items():
            if value != getattr(self, field):
                setattr(self, field, value)
                self.status = self.Status.PLANNED
        if not self.shows:
            self.broll_kind = self.person_shown = self.usage = self.result = ""
            self.needs = []


class Fix(models.Model):
    """What will change about a finished scene, from the shop owner's notes on it: one row of
    a fix plan they said yes to, recorded by code when it runs the plan (fix redesign; route
    B, #135, before it). Nothing is redone or paid for before the yes. It is the note its
    redo is made with, word for word."""

    class Redo(models.TextChoices):
        CLIP = "clip"
        PICTURE = "picture"
        LINE = "line"

    job = models.ForeignKey(Job, on_delete=models.CASCADE, related_name="fixes")
    scene = models.ForeignKey(Scene, on_delete=models.CASCADE, related_name="fixes")
    fix = models.TextField(
        help_text="The change, in one self-contained line, carrying everything the shop "
        "owner's notes on the scene ask for."
    )
    redo = models.CharField(
        max_length=10,
        choices=Redo.choices,
        help_text="Which step is made again for it: the clip, the starting picture (and then "
        "the clip), or the line. The shop owner isn't told.",
    )
    notes = models.JSONField(
        default=list,
        help_text="The shop owner's own words it was planned from, verbatim: each of their "
        "messages since the ad was last shown, or since they confirmed the scene's last Fix, "
        'as [{"seq", "text"}, ...].',
    )
    estimate_usd = models.DecimalField(
        max_digits=10, decimal_places=6, help_text="What its redo was estimated to cost."
    )
    asked = models.ForeignKey(
        "agents.ToolCall",
        on_delete=models.CASCADE,
        related_name="fixes",
        help_text="The call that recorded it: code's run of the fix plan.",
    )
    confirmed_by = models.ForeignKey(
        "chat.Message",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        help_text="The shop owner's yes to the fix plan it is a row of.",
    )
    photos = models.JSONField(
        default=list,
        blank=True,
        help_text="The shop owner's photos the fix takes things from (Reference photo, "
        'CONTEXT.md), as [{"photo", "file", "thing", "use", "where"}, ...]: the photo\'s number '
        "in the chat, its key in the file store, the one thing to take from it, whether the "
        'thing goes in the starting picture ("picture") or only shows during the clip '
        '("clip"), and where the route call found it. Empty for a fix with no photo.',
    )
    drops = models.JSONField(
        default=list,
        blank=True,
        help_text="The things whose photos the scene stops using once the shop owner says yes, "
        "as their names. Empty for none.",
    )
    joined = models.BooleanField(
        default=False,
        help_text="Whether code added the fix because the scene shows a thing the shop "
        "owner's photo on another scene's note shows, or already uses a photo of it.",
    )
    brings_back = models.ForeignKey(
        "ProducedItem",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        help_text="For a fix that stops using a photo: the scene's starting picture or clip "
        "from before the photo, brought back for it with nothing paid. Blank when it is made "
        "again instead.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]
        constraints = [
            # A plan run again after a restart records the same Fix, not another.
            models.UniqueConstraint(fields=["asked", "scene"], name="one_fix_per_call_and_scene")
        ]

    def __str__(self) -> str:
        return f"Scene {self.scene.number} fix: {self.fix}"

    def cost_usd(self) -> Decimal:
        """What its redo has cost so far: every model call of the tool calls that started its
        steps, failed ones included, as they were paid for."""
        from gateway.models import ModelCall

        cost: Decimal | None = (
            ModelCall.objects.filter(tool_call__scene_steps__fix=self)
            .distinct()
            .aggregate(cost=Sum("cost_usd"))["cost"]
        )
        return cost or Decimal(0)


class FixPlan(models.Model):
    """What the decider plans to change in each scene from the shop owner's notes of one
    round (fix redesign, decision 19): shown to them with its cost, and run by code once
    they say yes. A chat has at most one plan waiting for their answer; a newer plan replaces
    it. Its scenes are `FixPlanScene` rows; a plan can have none, when nothing changes."""

    class State(models.TextChoices):
        PROPOSED = "proposed"
        CONFIRMED = "confirmed"
        DECLINED = "declined"
        REPLACED = "replaced"

    class Cant(models.TextChoices):
        # What the shop owner asked that isn't about any one scene (decision 13's judgment
        # can'ts); a scene's own can't is on its row.
        MUSIC = "music"
        VOICE = "voice"
        NUMBER_OF_SCENES = "number_of_scenes"
        EXAMPLE_VIDEO = "example_video"
        NOT_ABOUT_THE_AD = "not_about_the_ad"

    job = models.ForeignKey(Job, on_delete=models.CASCADE, related_name="fix_plans")
    session = models.ForeignKey("chat.Session", on_delete=models.CASCADE, related_name="fix_plans")
    notes = models.JSONField(
        default=list,
        help_text="The shop owner's own words it was planned from, verbatim: each of their "
        'messages of this round, since they last said yes, as [{"seq", "text"}, ...].',
    )
    state = models.CharField(max_length=10, choices=State.choices, default=State.PROPOSED)
    confirmed_by = models.ForeignKey(
        "chat.Message",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        help_text="The shop owner's yes to it. Blank until they say yes.",
    )
    question = models.TextField(
        blank=True, help_text="The one question for the shop owner. Blank for none."
    )
    cants = models.JSONField(
        default=list,
        blank=True,
        help_text="What the shop owner asked that can't be done and isn't about one scene, as "
        '[{"kind", "text"}, ...]: a `Cant`, and the sentence they are shown. Empty for none.',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["session"],
                condition=models.Q(state="proposed"),
                name="one_proposed_plan_per_session",
            )
        ]

    def __str__(self) -> str:
        return f"Fix plan {self.pk} ({self.state})"

    def estimate_usd(self) -> Decimal:
        """What its scenes are estimated to cost."""
        return sum((scene.estimate_usd for scene in self.scenes.all()), Decimal(0))

    def estimate_up_to_usd(self) -> Decimal:
        """The most its scenes can cost."""
        return sum((scene.estimate_up_to_usd for scene in self.scenes.all()), Decimal(0))

    def cost_usd(self) -> Decimal:
        """What its scenes have cost so far, as `FixPlanScene.cost_usd`."""
        from gateway.models import ModelCall

        cost: Decimal | None = (
            ModelCall.objects.filter(tool_call__scene_steps__plan_scene__plan=self)
            .distinct()
            .aggregate(cost=Sum("cost_usd"))["cost"]
        )
        return cost or Decimal(0)


class FixPlanScene(models.Model):
    """One scene of a fix plan: what changes in it, in plain words, and what that costs, or
    why it can't (fix redesign, flow step 5)."""

    class Action(models.TextChoices):
        NEW_PICTURE = "new_picture"
        NEW_CLIP = "new_clip"
        NEW_LINE = "new_line"
        GO_BACK = "go_back"
        NO_CHANGE = "no_change"

    plan = models.ForeignKey(FixPlan, on_delete=models.CASCADE, related_name="scenes")
    scene = models.ForeignKey(Scene, on_delete=models.CASCADE, related_name="plan_rows")
    action = models.CharField(max_length=20, choices=Action.choices)
    change = models.TextField(
        blank=True,
        help_text="What changes, in plain words: the note the step's prompt writer is given "
        "(decision 7), never a prompt.",
    )
    line = models.TextField(blank=True, help_text="The new line, for a new line. Blank else.")
    # A default in the database too, so a worker still on code from before this field can
    # still save a plan while the new code is rolled out.
    overlay = models.TextField(
        blank=True,
        default="",
        db_default="",
        help_text="The scene's new overlay, for a new line that changes what its overlay "
        "shows, such as the price. Blank keeps the overlay.",
    )
    go_back_to = models.PositiveIntegerField(
        null=True, blank=True, help_text="The version gone back to, for a go back. Blank else."
    )
    cant = models.TextField(
        blank=True, help_text="Why what the shop owner asked can't be done. Blank for none."
    )
    estimate_usd = models.DecimalField(
        max_digits=10, decimal_places=6, default=Decimal(0), help_text="What it should cost."
    )
    estimate_up_to_usd = models.DecimalField(
        max_digits=10,
        decimal_places=6,
        default=Decimal(0),
        help_text="The most it can cost, if the clip judge has the clip made again.",
    )
    photos = models.JSONField(
        default=list,
        blank=True,
        help_text='The shop owner\'s photos it uses (decision 21), as [{"photo", "file", '
        '"thing", "in_picture", "during_action"}, ...]: the photo\'s number in the chat, its '
        "key in the file store, the one thing to take from it, whether the thing is in the "
        "starting picture now, and whether more of it shows during the action.",
    )
    new_movement = models.BooleanField(
        default=False,
        help_text="For a new picture, whether it was a new clip, made a new picture because "
        "a photo's thing is in the starting picture: its clip moves as the change says, not "
        "as the clip it replaces.",
    )
    remakes_clip = models.BooleanField(
        default=False,
        help_text="For a go back, whether its clip is made again, paid, because the line "
        "changed since that version: the plan says so before the yes (decision 8). False "
        "when the picture and clip are brought back for nothing.",
    )
    fix = models.ForeignKey(
        Fix,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        help_text="The confirmed Fix code records for it once the shop owner says yes, which "
        "the worker's checks and the shop owner's photos go by. Blank for a go back, and until "
        "the yes.",
    )
    started_at = models.DateTimeField(
        null=True, blank=True, help_text="When code started it, after the yes. Blank until then."
    )
    brought_back = models.ForeignKey(
        "ProducedItem",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        help_text="For a go back, the starting picture brought back: what its clip, if it must "
        "be made again, is made from. Blank until it is gone back.",
    )
    landed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the scene's new version was made and passed its checks, or the shop "
        "owner said go ahead. Blank until then.",
    )
    error = models.TextField(
        blank=True, help_text="Why it couldn't be made, once it couldn't. Blank otherwise."
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["scene__number", "id"]
        constraints = [
            models.UniqueConstraint(fields=["plan", "scene"], name="one_row_per_plan_and_scene")
        ]

    def __str__(self) -> str:
        return f"Scene {self.scene.number} plan: {self.action}"

    def cost_usd(self) -> Decimal:
        """What it has cost so far: every model call of the tool calls that started its
        steps, failed ones included, as they were paid for. A model call is charged to a tool
        call, not a step, so a tool call starting steps for more than one row would count on
        each of them: every row's steps need tool calls of their own."""
        from gateway.models import ModelCall

        cost: Decimal | None = (
            ModelCall.objects.filter(tool_call__scene_steps__plan_scene=self)
            .distinct()
            .aggregate(cost=Sum("cost_usd"))["cost"]
        )
        return cost or Decimal(0)


class ReferencePhoto(models.Model):
    """A photo the shop owner attached to a note, kept on a scene that uses it (Reference
    photo, CONTEXT.md): the one thing to take from it, and a clean picture of only that thing,
    drawn from it once and sent in its place. It stays with the scene for every later fix, its
    starting pictures and its clips alike, until a fix the shop owner said yes to drops it or
    replaces it with a newer photo of the same thing (Julian 2026-10-15, D1 and D3), or going
    back sets it aside: then the scene uses the photos the version gone back to was made with
    (fix redesign, decision 8)."""

    class Use(models.TextChoices):
        # The thing is in the starting picture, or should be from the start: drawn into it.
        PICTURE = "picture"
        # The thing only shows during the clip: its clean picture goes to the video model.
        CLIP = "clip"

    scene = models.ForeignKey(Scene, on_delete=models.CASCADE, related_name="reference_photos")
    file = models.CharField(max_length=500, help_text="The shop owner's photo, in the file store.")
    thing = models.CharField(
        max_length=200, help_text='The one thing taken from it, such as "black toilet".'
    )
    use = models.CharField(max_length=10, choices=Use.choices)
    drawn = models.CharField(
        max_length=500,
        blank=True,
        help_text="The clean picture of only the thing, drawn from the photo, in the file "
        "store. Blank until it is drawn.",
    )
    added_by = models.ForeignKey(
        Fix, on_delete=models.CASCADE, related_name="+", help_text="The fix that added it."
    )
    dropped_by = models.ForeignKey(
        Fix,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="+",
        help_text="The fix that dropped it or replaced it. Blank while the scene uses it.",
    )
    set_aside_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When going back to a version made without it set it aside. Blank while the "
        "scene uses it, and again once going back to a version made with it.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]

    def __str__(self) -> str:
        return f"Scene {self.scene.number}'s photo of {self.thing}"


class SceneStep(models.Model):
    """One piece of a scene's work, run in the background: its starting picture, and later
    its audio, transcript and clip. A scene tool starts it and returns at once; when it
    finishes or fails, the producer is told on its next turn."""

    class Kind(models.TextChoices):
        STARTING_PICTURE = "starting_picture"
        LINE_AUDIO = "line_audio"
        TRANSCRIPT = "transcript"
        CLIP = "clip"

    class Way(models.IntegerChoices):
        """How a B-roll scene's clip is made (docs/broll-picture-logic.md, "The logic")."""

        FROM_PICTURE = 1, "From a starting picture"
        FROM_EXAMPLES = 3, "From example pictures"

    class Status(models.TextChoices):
        RUNNING = "running"
        FINISHED = "finished"
        STOPPED = "stopped"
        FAILED = "failed"

    scene = models.ForeignKey(Scene, on_delete=models.CASCADE, related_name="steps")
    kind = models.CharField(max_length=30, choices=Kind.choices)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.RUNNING)
    reason = models.TextField(blank=True, help_text="Why it failed or was stopped.")
    tool_call = models.ForeignKey(
        "agents.ToolCall",
        on_delete=models.CASCADE,
        related_name="scene_steps",
        help_text="The tool call that started it, which its model calls are charged to.",
    )
    line = models.TextField(help_text="The scene's line when the step started.")
    shows = models.TextField(
        blank=True,
        help_text="What the scene showed when the step started: blank for the person "
        "talking to camera.",
    )
    broll_kind = models.CharField(
        max_length=20,
        choices=Scene.BrollKind.choices,
        blank=True,
        help_text="The scene's B-roll kind when the step started.",
    )
    person_shown = models.CharField(
        max_length=20,
        choices=Scene.PersonShown.choices,
        blank=True,
        help_text="Whether the scene showed the presenter's face when the step started.",
    )
    usage = models.TextField(
        blank=True, help_text="How the scene used the product when the step started."
    )
    broll_result = models.TextField(
        blank=True, help_text="The result the scene ended on when the step started."
    )
    needs = models.JSONField(
        default=list,
        blank=True,
        help_text="What the scene needed that the main photo can't show when the step started.",
    )
    note = models.TextField(
        blank=True, help_text="What the producer asked for, beyond the line. Blank for nothing."
    )
    fix = models.ForeignKey(
        Fix,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="steps",
        help_text="The confirmed Fix the step was made for, if it fixes a finished scene. Blank "
        "for the ad's first making.",
    )
    plan_scene = models.ForeignKey(
        "FixPlanScene",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="steps",
        help_text="The confirmed fix plan's scene the step was made for. Blank for the ad's "
        "first making and for a Fix's steps. Its tool call starts steps for this row only, "
        "so the row's cost is its own.",
    )
    photo = models.ForeignKey(
        ProductPhoto,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        help_text="The product photo the starting picture was made from.",
    )
    photo_reason = models.TextField(blank=True, help_text="Why that photo suits the line.")
    prompt = models.TextField(blank=True, help_text="What the picture model was asked to make.")
    prompt_reason = models.TextField(blank=True, help_text="Why the prompt asks for that.")
    motion_prompt = models.TextField(
        blank=True,
        help_text="For a B-roll scene's starting picture, how the video model is asked to "
        "move it. Blank for a talking scene, whose clips all move the same way.",
    )
    way = models.PositiveSmallIntegerField(
        choices=Way.choices,
        null=True,
        blank=True,
        help_text="For a B-roll scene's starting picture, how its clip is made: 1, from a "
        "starting picture made from the main photo; 3, from example pictures, with no "
        "picture made. Blank for a talking scene, and for a B-roll scene planned before it "
        "had its B-roll labels.",
    )
    pictures_sent = models.JSONField(
        default=list,
        blank=True,
        help_text="For a B-roll scene's starting picture, the pictures sent, in order, each "
        'with its job, such as [{"image": 1, "photo": 3, "job": "the product, only how it looks"}, '
        '{"image": 2, "portrait": true, "job": "the presenter"}], the ad\'s before-picture last '
        'with "before_picture": true. Empty otherwise.',
    )
    reference_photos = models.ManyToManyField(
        ReferencePhoto,
        blank=True,
        related_name="+",
        help_text="For a B-roll scene's step, the shop owner's photos its scene used when it "
        "started, fixed then: a starting picture is drawn with those used in it, and a clip is "
        "made in reference mode with those that show only during it.",
    )
    made_from = models.ForeignKey(
        "ProducedItem",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
        help_text="What the step makes its item from, fixed when it starts: the voice that "
        "speaks a line's audio, or the audio a transcript is heard in or a clip speaks.",
    )
    picture = models.ForeignKey(
        "ProducedItem",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
        help_text="The starting picture a clip animates, fixed when it starts. Blank for a "
        "B-roll scene made way 3, which has none.",
    )
    picture_step = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
        help_text="For a clip, the starting picture step it was made from, fixed when it "
        "starts: for a B-roll scene made way 3, it holds the example pictures and the prompt.",
    )
    redraw_of = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="redraws",
        help_text="For a fix's starting picture drawn again because the picture before it "
        "failed its fix check, the step that drew that one. Blank otherwise.",
    )
    result = models.TextField(
        blank=True,
        help_text="What the producer is told when the step finishes or fails. Blank while it runs.",
    )
    started_at = models.DateTimeField(auto_now_add=True)
    seen_at = models.DateTimeField(
        default=timezone.now,
        help_text="The step's heartbeat: when it was started, then every "
        "STEP_HEARTBEAT_SECONDS while it works. A running step that hasn't been seen for "
        "STEP_DEAD_AFTER_SECONDS has died, and no longer keeps its session busy.",
    )
    finished_at = models.DateTimeField(
        null=True, blank=True, help_text="When it finished or failed. Blank while it runs."
    )
    producer_read_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the producer was first given the result. Until then the producer "
        "doesn't stop, so no result is lost.",
    )

    class Meta:
        ordering = ["started_at", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["scene", "kind"],
                condition=models.Q(status="running"),
                name="one_running_step_per_scene_and_kind",
            )
        ]

    def __str__(self) -> str:
        return f"Scene {self.scene.number} {self.kind} ({self.status})"

    def shown_before(self, kind: str) -> ProducedItem | None:
        """The scene's newest item of `kind` made before the step started: what the shop owner
        saw last, so an item the step made, or brought back, is never it."""
        return (
            self.scene.produced.filter(kind=kind, created_at__lt=self.started_at)
            .order_by("-version")
            .first()
        )

    @staticmethod
    def made_for(scene: Scene, through: str = "") -> dict[str, Any]:
        """What a step made for `scene` as it stands has: its line, what it shows and its
        B-roll labels. A step is started with them, and they filter steps, or what steps
        made `through` a relation to them, such as "step__", to those still up to date."""
        return {
            f"{through}line": scene.line,
            f"{through}shows": scene.shows,
            **{
                f"{through}{STEP_BROLL_FIELDS[field]}": getattr(scene, field)
                for field in BROLL_FIELDS
            },
        }


class ProducedItem(models.Model):
    """Something a model made for a job, such as the portrait or the voice. Each belongs to
    the job or to one scene, and a remake is a new version: nothing is overwritten."""

    class Kind(models.TextChoices):
        PORTRAIT = "portrait"
        VOICE = "voice"
        STARTING_PICTURE = "starting_picture"
        LINE_AUDIO = "line_audio"
        TRANSCRIPT = "transcript"
        CLIP = "clip"
        FINISHED_AD = "finished_ad"
        MUSIC = "music"
        # The place the ad's B-roll scenes happen in, before any starts: every B-roll
        # starting picture is drawn from it.
        BEFORE_PICTURE = "before_picture"

    job = models.ForeignKey(Job, on_delete=models.CASCADE, related_name="produced")
    scene = models.ForeignKey(
        Scene,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="produced",
        help_text="Blank when it belongs to the whole job.",
    )
    step = models.ForeignKey(
        SceneStep,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="produced",
        help_text="The scene step that made it. Blank for what belongs to the whole job.",
    )
    kind = models.CharField(max_length=20, choices=Kind.choices)
    version = models.PositiveSmallIntegerField(default=1)
    file = models.CharField(
        max_length=500,
        blank=True,
        help_text="Key in the file store: the picture, the line's audio, the clip, the "
        "finished ad, the music, or the voice's measuring sample. Blank for a voice not "
        "measured yet, and for a transcript.",
    )
    voice_id = models.CharField(max_length=200, blank=True)
    words_per_second = models.FloatField(
        null=True, blank=True, help_text="The voice's measured speaking speed."
    )
    made_from = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
        help_text="What it was made from: for a line's audio, the voice that spoke it; for a "
        "transcript, the audio it was heard in; for a clip, the audio it speaks; for a "
        "finished ad, the music under its voice.",
    )
    picture = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
        help_text="The starting picture a clip animates.",
    )
    seconds = models.FloatField(
        null=True,
        blank=True,
        help_text="How long the audio, the clip, the finished ad or the music lasts.",
    )
    text = models.TextField(
        blank=True,
        help_text="The transcript, exactly as it was heard, what the music was asked to "
        "sound like, or what the before-picture was asked to show.",
    )
    words = models.JSONField(
        default=list,
        blank=True,
        help_text="Each word heard, with when it starts and ends in seconds: "
        '[{"text", "start", "end"}, ...].',
    )
    cuts = models.JSONField(
        default=list,
        blank=True,
        help_text="For a finished ad, each scene in the order it plays: its clip, the part of "
        "the clip kept, and where that part plays in the ad, in seconds: "
        '[{"scene", "clip", "clip_start", "clip_end", "start", "end"}, ...].',
    )
    captions = models.JSONField(
        default=list,
        blank=True,
        help_text="For a finished ad, the words drawn along its bottom as they were spoken, "
        'a few at a time, and when each shows, in seconds: [{"text", "start", "end"}, ...].',
    )
    ruling = models.JSONField(
        null=True,
        blank=True,
        help_text="For a B-roll clip, the clip judge's ruling (jobs.clip_judge.judged): its "
        "verdict, each check's, how many answers failed, each failed answer's problems and "
        "whole answer, the problems let go, every answer, and the pictures it was shown. "
        "Blank for a clip not judged.",
    )
    remake = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        help_text="For a B-roll clip made again by code because the clip judge failed the one "
        "before it (the auto-fix, #155): which remake of its step's clip it is, 1 or 2.",
    )
    motion_prompt = models.TextField(
        blank=True,
        db_default="",
        help_text="For a remade clip, the video prompt written for it from what the judge saw "
        "wrong.",
    )
    change = models.TextField(
        blank=True,
        db_default="",
        help_text="For a starting picture or clip, the change it was made for, in the shop "
        'owner\'s words, as its scene version\'s label ("black toilet"), or "back to v2" for '
        "one brought back by going back. Blank for the ad's first making.",
    )
    back_to = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        help_text="For a starting picture or clip brought back by going back, the scene "
        "version gone back to. Blank otherwise.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["job", "kind", "version"]
        constraints = [
            models.UniqueConstraint(
                fields=["job", "scene", "kind", "version"],
                name="one_item_per_version",
                nulls_distinct=False,
            )
        ]

    def __str__(self) -> str:
        return f"{self.kind} v{self.version}"


class FixCheck(models.Model):
    """The check of a fix (route B step 6, #131): what a judge, asked the complaint back,
    found in the new starting picture or clip a fix made, shown beside the old one. Kept
    with the fix and both versions, as a case for the complaint log."""

    class Verdict(models.TextChoices):
        PASSED = "passed"
        FAILED = "failed"
        # Too few of the judge's asks were answered to agree.
        UNCHECKED = "unchecked"

    fix = models.ForeignKey(Fix, on_delete=models.CASCADE, related_name="checks")
    step = models.OneToOneField(
        SceneStep,
        on_delete=models.CASCADE,
        related_name="fix_check",
        help_text="The step that made the new version for the fix.",
    )
    old = models.ForeignKey(
        ProducedItem,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
        help_text="The scene's version before the fix, which the shop owner noted.",
    )
    new = models.ForeignKey(
        ProducedItem,
        on_delete=models.PROTECT,
        related_name="+",
        help_text="The version the fix made.",
    )
    verdict = models.CharField(max_length=10, choices=Verdict.choices)
    reason = models.TextField(blank=True, help_text="When it failed: what is still wrong.")
    answers = models.JSONField(
        default=list, blank=True, help_text="Each answer the judge gave, as it gave it."
    )
    notice = models.ForeignKey(
        "chat.Message",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        help_text="For a failed check, the notice that showed the shop owner both versions and "
        "why: what they answer after it decides what happens next. Blank until it is posted.",
    )
    gone_ahead_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="For a failed check of a fix plan's step, when the shop owner said go ahead "
        "with it anyway. Blank otherwise.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]

    def __str__(self) -> str:
        return f"Scene {self.step.scene.number} fix check: {self.verdict}"

    @property
    def what(self) -> str:
        """What the fix made again: "picture" or "clip"."""
        return "picture" if self.step.kind == SceneStep.Kind.STARTING_PICTURE else "clip"
