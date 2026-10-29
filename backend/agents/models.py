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
    agent_fix = models.ForeignKey(
        "jobs.AgentFix",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="tool_calls",
        help_text="For the fixing agent, the scene fix it called the tool for. Blank otherwise.",
    )
    tool = models.CharField(max_length=50)
    call_id = models.CharField(
        max_length=200, help_text="The model's id for the call, which pairs it with its result."
    )
    arguments = models.JSONField(help_text="The tool's own arguments, without its `why`.")
    why = models.TextField(
        blank=True,
        help_text="The one sentence the agent wrote with the call saying why it made it (spec "
        "#247 point 7). Blank for an agent whose tools ask for none.",
    )
    result = models.TextField(
        blank=True, help_text="What the tool handed back to the agent. Blank until it finishes."
    )
    answer = models.CharField(
        max_length=10,
        blank=True,
        choices=[("made", "made"), ("cant", "can't"), ("question", "question")],
        help_text="Which of the three ways its result answers the agent (loop.ToolAnswer): the "
        "thing it made, can't with the reason, or one question. Blank until it finishes.",
    )
    fold_line = models.TextField(
        blank=True,
        help_text="The one line the call stands as in the agent's conversation once it is "
        "folded (spec #247 point 15): tool, scene, outcome, cost. Blank while it is given in "
        "full.",
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
    """What code tells the producer: the page the shop owner pasted in the page panel, which
    code keeps as the page, the shop owner's pause: the steps it cancelled and those it
    kept running (spec #247 point 14), and a photo sent with a scene's note that the producer
    went to end its turn without using (agents/note_photos.py). Short and written by code.
    The shop owner isn't shown them: the chat's own messages tell them."""

    class Kind(models.TextChoices):
        PAGE_PASTED = "page_pasted"
        PAUSED = "paused"
        NOTE_PHOTO = "note_photo"

    session = models.ForeignKey(
        "chat.Session", on_delete=models.CASCADE, related_name="system_notes"
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


class RefusedPicture(models.Model):
    """A picture an agent's model refused to look at: its safety filter refused the agent's
    turn until the picture was left out (requirement 22). Every later turn of that agent in
    the session leaves it out too, so the filter isn't asked again, and says so."""

    session = models.ForeignKey(
        "chat.Session", on_delete=models.CASCADE, related_name="refused_pictures"
    )
    purpose = models.CharField(
        max_length=50, help_text='The agent\'s purpose, which picks its model, as "fix_scene".'
    )
    label = models.CharField(max_length=500, help_text="The label code gave the picture.")
    key = models.CharField(max_length=500, help_text="The picture's key in the file store.")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]

    def __str__(self) -> str:
        return f"{self.purpose}: {self.label}"
