from django.db import models


class ModelCall(models.Model):
    """One attempt at one model call. The only source of cost and time numbers."""

    class Outcome(models.TextChoices):
        SUCCEEDED = "succeeded"
        FAILED = "failed"

    job = models.ForeignKey(
        "jobs.Job", null=True, blank=True, on_delete=models.PROTECT, related_name="model_calls"
    )
    tool_call = models.ForeignKey(
        "agents.ToolCall",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="model_calls",
        help_text="The tool call this was made for. Blank for an agent's own turns.",
    )
    purpose = models.CharField(max_length=50)
    provider = models.CharField(max_length=30)
    model = models.CharField(max_length=100)
    attempt = models.PositiveSmallIntegerField()
    handoff = models.JSONField()
    images = models.JSONField(
        default=list,
        blank=True,
        help_text="The pictures shown with the handoff: each one's label and file-store key.",
    )
    output = models.JSONField(null=True, blank=True)
    outcome = models.CharField(max_length=10, choices=Outcome.choices)
    error = models.TextField(blank=True)
    input_tokens = models.PositiveIntegerField(null=True, blank=True)
    output_tokens = models.PositiveIntegerField(null=True, blank=True)
    characters = models.PositiveIntegerField(
        null=True, blank=True, help_text="Characters spoken, for voice models billed by them."
    )
    cost_usd = models.DecimalField(max_digits=10, decimal_places=6, null=True, blank=True)
    duration_ms = models.PositiveIntegerField()
    decision = models.CharField(max_length=50, blank=True)
    reason = models.TextField(blank=True, help_text="The one-sentence judgement, for judgements.")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]

    def __str__(self) -> str:
        return f"{self.purpose} via {self.model} (attempt {self.attempt}, {self.outcome})"
