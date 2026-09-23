import uuid

from django.db import models


class Job(models.Model):
    class Status(models.TextChoices):
        QUEUED = "queued"
        READING_PAGE = "reading_page"
        PAGE_READ = "page_read"
        PLANNING = "planning"
        PLANNED = "planned"
        MAKING_PERSON = "making_person"
        CHECKING_PLAN = "checking_plan"
        # The plan passed its checks, so nothing will need rewriting once rendering starts.
        READY_TO_RENDER = "ready_to_render"
        # Waiting for the user: the link didn't lead to one product's readable page.
        NEEDS_WORKING_LINK = "needs_working_link"
        # Waiting for the user: the page gave no product photo we could use.
        NEEDS_PRODUCT_PHOTOS = "needs_product_photos"
        # Waiting for the user to answer a question from the producer or a planning check.
        NEEDS_ANSWER = "needs_answer"
        FAILED = "failed"

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
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.QUEUED)
    page_text = models.TextField(
        blank=True,
        help_text="The words a visitor sees, then the product data the page declares for "
        "search engines. Model calls read this.",
    )
    page_html_key = models.CharField(
        max_length=500,
        blank=True,
        help_text="Key in the file store of the page's original HTML, exactly as served.",
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

    def open_question(self) -> Question | None:
        """The question the job is waiting on the user to answer, if any."""
        return self.questions.filter(answered_at__isnull=True).first()


class ActivityEntry(models.Model):
    """One step shown in the live activity view: what happened, and one sentence on why."""

    job = models.ForeignKey(Job, on_delete=models.CASCADE, related_name="activity")
    seq = models.PositiveIntegerField(help_text="1, 2, 3... within the job, in the order written.")
    message = models.TextField()
    reason = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["job", "seq"]
        constraints = [models.UniqueConstraint(fields=["job", "seq"], name="one_entry_per_seq")]
        verbose_name_plural = "activity entries"

    def __str__(self) -> str:
        return f"#{self.seq} {self.message}"


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


class Question(models.Model):
    """Something the job asked the user, and their answer. A job waits on at most one."""

    class Kind(models.TextChoices):
        WORKING_LINK = "working_link"
        PRODUCT_PHOTOS = "product_photos"
        PRODUCER = "producer"
        # Planning checks. Answering these goes back to the checks, not to planning.
        UNCLEAR_PAGE = "unclear_page"
        FACT_CHECK = "fact_check"
        LENGTH = "length"

    class LineChoice(models.TextChoices):
        """The answers to a fact-check question about a line."""

        KEEP = "keep", "Keep this line"
        OWN = "own", "Use my own line"

    job = models.ForeignKey(Job, on_delete=models.CASCADE, related_name="questions")
    kind = models.CharField(max_length=20, choices=Kind.choices)
    question = models.TextField()
    reason = models.TextField(help_text="One sentence on why the job had to ask.")
    options = models.JSONField(
        default=list, blank=True, help_text="The choices offered. Empty means a typed answer."
    )
    # Kept when shortening the script drops the scene: every exchange with the user is.
    scene = models.ForeignKey(
        Scene,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        help_text="The scene whose line the question is about, if any.",
    )
    answer = models.TextField(
        blank=True, help_text="The user's words, the new link, or the photos they uploaded."
    )
    asked_at = models.DateTimeField(auto_now_add=True)
    answered_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["job", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["job"],
                condition=models.Q(answered_at__isnull=True),
                name="one_open_question_per_job",
            )
        ]

    def __str__(self) -> str:
        return self.question


class ProducedItem(models.Model):
    """Something a model made for a job, such as the portrait or the voice. Each belongs to
    the job or to one scene, and a remake is a new version: nothing is overwritten."""

    class Kind(models.TextChoices):
        PORTRAIT = "portrait"
        VOICE = "voice"

    job = models.ForeignKey(Job, on_delete=models.CASCADE, related_name="produced")
    scene = models.ForeignKey(
        Scene,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="produced",
        help_text="Blank when it belongs to the whole job.",
    )
    kind = models.CharField(max_length=20, choices=Kind.choices)
    version = models.PositiveSmallIntegerField(default=1)
    file = models.CharField(
        max_length=500,
        blank=True,
        help_text="Key in the file store: the portrait, or the voice's measuring sample. "
        "Blank for a voice not measured yet.",
    )
    voice_id = models.CharField(max_length=200, blank=True)
    words_per_second = models.FloatField(
        null=True, blank=True, help_text="The voice's measured speaking speed."
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
