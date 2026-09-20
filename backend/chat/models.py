import uuid

from django.db import models


class Session(models.Model):
    """One conversation thread, named and returned to. It holds every message and every
    job made in it."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(
        max_length=200,
        blank=True,
        help_text="Taken from the user's first message, until they rename it. Blank until "
        "they have said something.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return self.name or f"Session {self.pk}"


class Message(models.Model):
    """One turn in a session, from the user or the agent. The only way the two talk."""

    class Role(models.TextChoices):
        USER = "user"
        AGENT = "agent"

    session = models.ForeignKey(Session, on_delete=models.CASCADE, related_name="messages")
    seq = models.PositiveIntegerField(
        help_text="1, 2, 3... within the session, in the order they were said. What a poll "
        "asks for what has happened since."
    )
    role = models.CharField(max_length=10, choices=Role.choices)
    text = models.TextField(blank=True, help_text="Blank when the message only carries files.")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["session", "seq"]
        constraints = [
            models.UniqueConstraint(fields=["session", "seq"], name="one_message_per_seq")
        ]

    def __str__(self) -> str:
        return f"#{self.seq} {self.role}: {self.text[:60]}"


class Attachment(models.Model):
    """A file a message carries: a photo the user attached, or something the agent made,
    such as the portrait, the person's voice or the finished ad."""

    class Kind(models.TextChoices):
        PICTURE = "picture"
        SOUND = "sound"
        VIDEO = "video"

    message = models.ForeignKey(Message, on_delete=models.CASCADE, related_name="attachments")
    position = models.PositiveSmallIntegerField(help_text="1, 2, 3... in the order shown.")
    kind = models.CharField(max_length=10, choices=Kind.choices)
    file = models.CharField(max_length=500, help_text="Key in the file store.")

    class Meta:
        ordering = ["message", "position"]
        constraints = [
            models.UniqueConstraint(
                fields=["message", "position"], name="one_attachment_per_position"
            )
        ]

    def __str__(self) -> str:
        return f"{self.kind} {self.position}"
