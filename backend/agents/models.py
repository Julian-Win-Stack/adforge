from decimal import Decimal

from django.db import models
from django.db.models import Sum


class ToolCall(models.Model):
    """A checkpoint: one tool an agent called, what it was called with, and what it
    produced. It is written before the tool runs and marked finished after, so a restart
    knows which tools finished and runs any that didn't again."""

    session = models.ForeignKey("chat.Session", on_delete=models.CASCADE, related_name="tool_calls")
    job = models.ForeignKey(
        "jobs.Job",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="tool_calls",
        help_text="The job the tool worked on. Blank for a tool that works on no job.",
    )
    agent = models.CharField(max_length=50, help_text='Which agent called it, such as "producer".')
    tool = models.CharField(max_length=50)
    call_id = models.CharField(
        max_length=200, help_text="The model's id for the call, which pairs it with its result."
    )
    arguments = models.JSONField()
    result = models.TextField(
        blank=True, help_text="What the tool handed back to the agent. Blank until it finishes."
    )
    asked_about = models.CharField(
        max_length=50,
        blank=True,
        help_text='What its result has the agent ask the shop owner, such as "length" or '
        '"scene 2", so a tool can tell whether they have answered since. Blank for nothing.',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["created_at", "id"]
        constraints = [
            models.UniqueConstraint(fields=["session", "call_id"], name="one_checkpoint_per_call")
        ]

    def __str__(self) -> str:
        return f"{self.agent} called {self.tool}"

    @property
    def finished(self) -> bool:
        return self.finished_at is not None

    def cost_usd(self) -> Decimal:
        """What the tool's work cost: the sum of the model calls made for it."""
        cost: Decimal | None = self.model_calls.aggregate(cost=Sum("cost_usd"))["cost"]
        return cost or Decimal(0)
