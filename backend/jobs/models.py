import uuid

from django.db import models


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
        help_text="The words a visitor sees, then the product data the page declares for "
        "search engines. Model calls read this.",
    )
    page_html_key = models.CharField(
        max_length=500,
        blank=True,
        help_text="Key in the file store of the page's original HTML, exactly as served. "
        "Blank until the page has been found to show its product and its photos are kept.",
    )
    product_colour = models.CharField(
        max_length=100,
        blank=True,
        help_text="The colour the ad shows the product in, picked by the producer from the "
        "product photos.",
    )
    person_looks = models.TextField(
        blank=True, help_text="The producer's description of the person the portrait shows."
    )
    person_voice = models.TextField(
        blank=True, help_text="The producer's description of the person's voice."
    )
    length_choice = models.CharField(
        max_length=20,
        choices=LengthChoice.choices,
        blank=True,
        help_text="What the user chose when the script didn't fit the target length.",
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

    class Meta:
        ordering = ["job", "position"]
        constraints = [
            models.UniqueConstraint(fields=["job", "position"], name="one_photo_per_position")
        ]

    def __str__(self) -> str:
        return self.source_url


class Scene(models.Model):
    class Status(models.TextChoices):
        PLANNED = "planned"
        # Its clip is made.
        FINISHED = "finished"

    job = models.ForeignKey(Job, on_delete=models.CASCADE, related_name="scenes")
    number = models.PositiveSmallIntegerField(help_text="1, 2, 3... in the order they play.")
    line = models.TextField(help_text="What the person says in this scene.")
    fact_checked = models.BooleanField(
        default=False, help_text="The line passed the fact check, or the user kept it."
    )
    fact_problems = models.JSONField(
        default=list,
        blank=True,
        help_text="Why the fact check failed this line each time, oldest first. Two rewrites "
        "are tried before the user is asked.",
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PLANNED)

    class Meta:
        ordering = ["job", "number"]
        constraints = [
            models.UniqueConstraint(fields=["job", "number"], name="one_scene_per_number")
        ]

    def __str__(self) -> str:
        return f"Scene {self.number}: {self.line}"

    def change_line(self, line: str) -> None:
        """Give the scene a new line, unsaved. A clip says the line it was made for, so a
        finished scene is planned again: it needs a new clip."""
        if line != self.line:
            self.line = line
            self.status = self.Status.PLANNED


class SceneStep(models.Model):
    """One piece of a scene's work, run in the background: its starting picture, and later
    its audio, transcript and clip. A scene tool starts it and returns at once; when it
    finishes or fails, the producer is told on its next turn."""

    class Kind(models.TextChoices):
        STARTING_PICTURE = "starting_picture"
        LINE_AUDIO = "line_audio"
        TRANSCRIPT = "transcript"
        CLIP = "clip"

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
        help_text="The starting picture a clip animates, fixed when it starts.",
    )
    result = models.TextField(
        blank=True,
        help_text="What the producer is told when the step finishes or fails. Blank while it runs.",
    )
    started_at = models.DateTimeField(auto_now_add=True)
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
        "transcript, the audio it was heard in; for a clip, the audio it speaks.",
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
