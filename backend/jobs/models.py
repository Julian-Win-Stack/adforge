import uuid
from typing import Any

from django.db import models
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
        help_text="The product's name as the ad says it, from the plan. At least one scene "
        "where the person talks to camera says it.",
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
    second_way_of_use = models.BooleanField(
        default=False,
        help_text="For a B-roll scene, whether it shows the second of two ways the product can "
        "be used, the first being the scene just before it: so its clip is made to match that "
        "one's, with the same person and place.",
    )
    broll_kind = models.CharField(
        max_length=20,
        choices=BrollKind.choices,
        blank=True,
        help_text='A B-roll scene\'s kind: "does a job" (the product does something you can '
        'see) or "showcase" (the product at its best). Blank for a talking scene, and for a '
        "B-roll scene planned before it was given.",
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
        '{"image": 2, "portrait": true, "job": "the presenter"}]. Empty otherwise.',
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
        help_text="The transcript, exactly as it was heard, or what the music was asked to "
        "sound like.",
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
