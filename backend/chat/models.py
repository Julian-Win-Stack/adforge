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
    # Both are only changed while holding the session row's lock, as messages are added.
    producer_running = models.BooleanField(
        default=False, help_text="A producer should be working in this session."
    )
    producer_seen_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="The producer's heartbeat. A producer that is running but hasn't been seen "
        "for PRODUCER_DEAD_AFTER_SECONDS has died.",
    )

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return self.name or f"Session {self.pk}"


class Message(models.Model):
    """One turn in a session, from the user or the agent, or a notice the code posts when a
    step fell back or failed, or a scene's starting picture or clip started being made. The
    only way the user and the agent talk; a notice is for the user alone, and no model is
    ever given it.

    A message about a scene's starting picture or clip names its `step`: a notice when the
    step starts, the message showing what it made, or a notice when it ended with nothing.
    So the chat can tell a scene's picture or clip is being made, from the messages alone.

    A message about a fix plan names it and what it says of it (`plan_event`): code's notice
    showing it, the shop owner's tap of its yes button, or code's notice that it is settled.
    So the chat can tell whether the plan's yes button is still on offer, from the messages
    alone."""

    class Role(models.TextChoices):
        USER = "user"
        AGENT = "agent"
        NOTICE = "notice"

    class Level(models.TextChoices):
        PROBLEM = "problem"  # Shown red: something went wrong that affects the ad.
        INFO = "info"  # Shown grey: normal, worth knowing.

    class StepKind(models.TextChoices):
        """The scene steps the chat follows: those whose result the shop owner sees."""

        STARTING_PICTURE = "starting_picture"
        CLIP = "clip"

    class StepState(models.TextChoices):
        STARTED = "started"
        MADE = "made"  # The message carries what it made.
        NOT_MADE = "not_made"  # It failed or stopped the run, with nothing made.

    class PlanEvent(models.TextChoices):
        """What a message says of a fix plan (`fix_plan`)."""

        SHOWN = "shown"  # Code's notice showing the plan, with the yes button.
        YES = "yes"  # The shop owner tapped the plan's yes button: the marker, never typed.
        CONFIRMED = "confirmed"  # Code's notice that the plan is going ahead.
        DECLINED = "declined"  # Code's notice that the plan was dropped.

    session = models.ForeignKey(Session, on_delete=models.CASCADE, related_name="messages")
    seq = models.PositiveIntegerField(
        help_text="1, 2, 3... within the session, in the order they were said. What a poll "
        "asks for what has happened since."
    )
    role = models.CharField(max_length=10, choices=Role.choices)
    level = models.CharField(
        max_length=10,
        choices=Level.choices,
        blank=True,
        help_text="How a notice is shown. Blank for the user's and the agent's messages.",
    )
    text = models.TextField(blank=True, help_text="Blank when the message only carries files.")
    step_scene = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        help_text="The number of the scene whose step this message is about. Null when it "
        "is about no step.",
    )
    # A default in the database too, so a worker still running code from before these
    # fields can add a message while the new code is being rolled out.
    step_kind = models.CharField(max_length=20, choices=StepKind.choices, blank=True, db_default="")
    step_state = models.CharField(
        max_length=10, choices=StepState.choices, blank=True, db_default=""
    )
    # The note a scene's card is filled with, for the shop owner to send or change, as when
    # the check of a fix found the change missing (#131). A default in the database too, as
    # for the step's fields.
    suggested_note = models.TextField(
        blank=True,
        db_default="",
        help_text="A note suggested for scene `suggested_note_scene`. Blank for none.",
    )
    suggested_note_scene = models.PositiveSmallIntegerField(
        null=True, blank=True, help_text="The scene the suggested note is about."
    )
    # A default in the database too, as for the step's fields.
    plan_event = models.CharField(
        max_length=10,
        choices=PlanEvent.choices,
        blank=True,
        db_default="",
        help_text="What the message says of `fix_plan`. Blank for a message about no plan, "
        "and for anything the shop owner typed: a typed yes is told apart from the button's.",
    )
    fix_plan = models.ForeignKey(
        "jobs.FixPlan",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="messages",
        help_text="The plan the message shows, answers or settles. Null for none.",
    )
    tries_of = models.ForeignKey(
        "jobs.SceneStep",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="tries_shown",
        help_text="The B-roll clip step whose every try the clip judge failed, for code's "
        "notice showing them to pick from (#180). Null for any other message.",
    )
    chosen_clip = models.ForeignKey(
        "jobs.ProducedItem",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="chosen_by",
        help_text='For the shop owner\'s tap of "Use this one" under a try of a clip the judge '
        "failed every try of, the try they chose (#180): the marker, never typed. Null "
        "otherwise.",
    )
    routed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When code sent the shop owner's message, once the ad had a clip, to whoever "
        "answers it: the decider, the producer, or code for a tap of a plan's yes button. "
        "Blank until then, and for messages from before the first clip, which the producer "
        "reads as they come.",
    )
    trace_id = models.CharField(
        max_length=32,
        blank=True,
        help_text="The Langfuse trace of the agent's work on the user's message, which each "
        "time it works on it adds to. Blank when not traced.",
    )
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
    such as the portrait, the person's voice, a scene's starting picture or clip, or the
    finished ad."""

    class Kind(models.TextChoices):
        PICTURE = "picture"
        SOUND = "sound"
        VIDEO = "video"

    message = models.ForeignKey(Message, on_delete=models.CASCADE, related_name="attachments")
    position = models.PositiveSmallIntegerField(help_text="1, 2, 3... in the order shown.")
    kind = models.CharField(max_length=10, choices=Kind.choices)
    file = models.CharField(max_length=500, help_text="Key in the file store.")
    scene = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        help_text="The number of the scene this is the starting picture or clip of. Null for "
        "a file that belongs to no scene: the portrait, the voice, the finished ad, a photo.",
    )
    version = models.TextField(
        blank=True,
        db_default="",
        help_text="For a scene's starting picture or clip, the label of the scene version it "
        'is shown as, such as "v2 black toilet". Blank otherwise.',
    )

    class Meta:
        ordering = ["message", "position"]
        constraints = [
            models.UniqueConstraint(
                fields=["message", "position"], name="one_attachment_per_position"
            )
        ]

    def __str__(self) -> str:
        return f"{self.kind} {self.position}"
