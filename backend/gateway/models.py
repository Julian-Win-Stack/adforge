from django.db import models


class SystemPrompt(models.Model):
    """What an agent or model was told for its calls in a session: its instructions and the
    tools it was offered. Kept once, and again only when either changes, so every call
    names the one it was given without each holding its own copy."""

    session = models.ForeignKey(
        "chat.Session",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="system_prompts",
        help_text="The session its calls were for. Blank for calls made outside one.",
    )
    purpose = models.CharField(max_length=50)
    instructions = models.TextField(blank=True)
    tools = models.JSONField(
        default=list,
        blank=True,
        help_text="Each tool offered: its name, its description and its arguments' JSON schema.",
    )
    digest = models.CharField(
        max_length=64, help_text="The SHA-256 of the instructions and tools, to find it by."
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]
        # One per session, purpose and text, even for judge asks sent at once from threads.
        constraints = [
            models.UniqueConstraint(
                fields=["session", "purpose", "digest"],
                nulls_distinct=False,
                name="one_system_prompt_per_session_purpose_and_text",
            )
        ]

    def __str__(self) -> str:
        return f"{self.purpose}'s system prompt ({self.digest[:8]})"


class ModelCall(models.Model):
    """One attempt at one model call. The only source of cost and time numbers."""

    class Outcome(models.TextChoices):
        SUCCEEDED = "succeeded"
        FAILED = "failed"

    session = models.ForeignKey(
        "chat.Session",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="model_calls",
        help_text="The session it was for, so a session's whole cost adds up. Blank only for "
        "calls made before sessions existed.",
    )
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
    system_prompt = models.ForeignKey(
        SystemPrompt,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="model_calls",
        help_text="What the model was told: blank for a call given no instructions or tools.",
    )
    provider = models.CharField(max_length=30)
    model = models.CharField(max_length=100)
    attempt = models.PositiveSmallIntegerField()
    handoff = models.JSONField()
    images = models.JSONField(
        default=list,
        blank=True,
        help_text="The pictures shown with the handoff: each one's label and file-store key.",
    )
    video = models.CharField(
        max_length=500,
        blank=True,
        help_text="The video shown with the handoff, by its file-store key, as it was sent: "
        "blank for a call shown none.",
    )
    output = models.JSONField(null=True, blank=True)
    outcome = models.CharField(max_length=10, choices=Outcome.choices)
    error = models.TextField(blank=True)
    input_tokens = models.PositiveIntegerField(null=True, blank=True)
    output_tokens = models.PositiveIntegerField(null=True, blank=True)
    characters = models.PositiveIntegerField(
        null=True, blank=True, help_text="Characters spoken, for voice models billed by them."
    )
    audio_seconds = models.FloatField(
        null=True,
        blank=True,
        help_text="Seconds of audio heard or made, for models billed by them.",
    )
    video_seconds = models.FloatField(
        null=True, blank=True, help_text="Seconds of video made, for models billed by them."
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
