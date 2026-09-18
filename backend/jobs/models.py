import uuid

from django.db import models


class Job(models.Model):
    class Status(models.TextChoices):
        QUEUED = "queued"
        READING_PAGE = "reading_page"
        PAGE_READ = "page_read"
        PLANNING = "planning"
        PLANNED = "planned"
        # Waiting for the user: the link didn't lead to one product's readable page.
        NEEDS_WORKING_LINK = "needs_working_link"
        # Waiting for the user: the page gave no product photo we could use.
        NEEDS_PRODUCT_PHOTOS = "needs_product_photos"
        # Waiting for the user: the producer asked something the page doesn't settle.
        NEEDS_ANSWER = "needs_answer"
        FAILED = "failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
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
    brand_colours = models.JSONField(
        default=list, blank=True, help_text='The brand colours from the plan, as "#RRGGBB".'
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.product_url} ({self.status})"


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

    class Meta:
        ordering = ["job", "position"]

    def __str__(self) -> str:
        return self.source_url


class Scene(models.Model):
    class Status(models.TextChoices):
        PLANNED = "planned"

    job = models.ForeignKey(Job, on_delete=models.CASCADE, related_name="scenes")
    number = models.PositiveSmallIntegerField(help_text="1, 2, 3... in the order they play.")
    line = models.TextField(help_text="What the person says in this scene.")
    slot_seconds = models.PositiveSmallIntegerField(help_text="How long the scene lasts.")
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

    job = models.ForeignKey(Job, on_delete=models.CASCADE, related_name="questions")
    kind = models.CharField(max_length=20, choices=Kind.choices)
    question = models.TextField()
    reason = models.TextField(help_text="One sentence on why the job had to ask.")
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
