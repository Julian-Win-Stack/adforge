import uuid

from django.db import models


class Job(models.Model):
    class Status(models.TextChoices):
        QUEUED = "queued"
        READING_PAGE = "reading_page"
        PAGE_READ = "page_read"
        FAILED = "failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    product_url = models.URLField(max_length=2000)
    target_seconds = models.PositiveSmallIntegerField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.QUEUED)
    page_text = models.TextField(blank=True, help_text="The page's visible text, exactly as read.")
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
    source_url = models.URLField(max_length=2000)
    file = models.CharField(max_length=500, help_text="Key in the file store.")

    class Meta:
        ordering = ["job", "position"]

    def __str__(self) -> str:
        return self.source_url
