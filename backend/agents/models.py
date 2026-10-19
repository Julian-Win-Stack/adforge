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
    pictures = models.JSONField(
        default=list,
        blank=True,
        help_text="The pictures it handed back with its result, in the order the agent is "
        "shown them: each one's label, written by code, and its file-store key.",
    )
    asked_about = models.CharField(
        max_length=50,
        blank=True,
        help_text='What its result has the agent ask the shop owner, such as "length" or '
        '"scene 2", so a tool can tell whether they have answered since. Blank for nothing.',
    )
    trace_id = models.CharField(
        max_length=32,
        blank=True,
        help_text="The Langfuse trace its latest run is in, so a scene step it started, run "
        "elsewhere, is traced inside it. Blank when tracing is off.",
    )
    observation_id = models.CharField(
        max_length=16, blank=True, help_text="Its latest run's own ID in that trace."
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


class SystemNote(models.Model):
    """What code tells the producer of a fix plan it runs (fix redesign, decision 20): the plan
    with its per-scene lines, the fixes started, a scene done and what it cost, a failed check
    and its two versions, an error, the new ad shown. And of the page the shop owner pasted in
    the page panel, which code keeps as the page. Short and written by code; never a
    picture, frames, judge words, version lists or the decider's reasoning. The shop owner
    isn't shown them: the chat's own messages tell them."""

    class Kind(models.TextChoices):
        PLAN_SHOWN = "plan_shown"
        FIXES_STARTED = "fixes_started"
        SCENE_DONE = "scene_done"
        CHECK_FAILED = "check_failed"
        ERROR = "error"
        AD_SHOWN = "ad_shown"
        PLAN_DECLINED = "plan_declined"
        YES_TOO_LATE = "yes_too_late"
        CHECK_UNCHECKED = "check_unchecked"
        PAGE_PASTED = "page_pasted"

    session = models.ForeignKey(
        "chat.Session", on_delete=models.CASCADE, related_name="system_notes"
    )
    plan = models.ForeignKey(
        "jobs.FixPlan",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="system_notes",
        help_text="The fix plan the note is about. Null for a note about no plan, as the page "
        "the shop owner pasted.",
    )
    kind = models.CharField(max_length=20, choices=Kind.choices)
    text = models.TextField()
    read_at = models.DateTimeField(
        null=True, blank=True, help_text="When the producer was given it. Blank until then."
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]

    def __str__(self) -> str:
        return f"{self.kind}: {self.text}"
