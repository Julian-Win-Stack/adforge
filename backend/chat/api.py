import mimetypes
import uuid

from django.core.files.uploadedfile import UploadedFile
from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework import serializers, status
from rest_framework.decorators import api_view
from rest_framework.request import Request
from rest_framework.response import Response

from adforge import file_store
from agents.tasks import another_session_is_busy, hold_one_at_a_time_lock, wake_producer
from gateway.gateway import IMAGE_TYPE_NAMES, IMAGE_TYPES
from jobs import work
from jobs.fix_plans import notice
from jobs.models import FixPlan, ProducedItem
from jobs.page import MAX_PHOTO_BYTES, MAX_PHOTOS

from . import messages
from .models import Attachment, Message, Session

# As much as one message may say. Long enough for a brief pasted in one go.
MAX_TEXT_CHARACTERS = 10_000

# What the user is told when another session is working: only one works at a time.
ANOTHER_SESSION_IS_BUSY = "Another ad is being made right now. Please try again once it's finished."

# What the yes button is told when the plan it was tapped on no longer waits for an answer.
PLAN_NO_LONGER_WAITS = "That plan isn't waiting for an answer any more. Look at the newer plan."
# What it is told when the plan already has a yes from it, as after a reply that was lost.
PLAN_ALREADY_SAID_YES = "You've already said yes to that plan."
# What a tap of the yes button says, as the chat and the producer read it.
TAPPED_YES = "Yes"


class AttachmentSerializer(serializers.ModelSerializer[Attachment]):
    url = serializers.SerializerMethodField()

    class Meta:
        model = Attachment
        fields = ["position", "kind", "url", "scene", "version"]

    def get_url(self, attachment: Attachment) -> str:
        return file_store.url(attachment.file)


class MessageSerializer(serializers.ModelSerializer[Message]):
    attachments = AttachmentSerializer(many=True, read_only=True)
    step = serializers.SerializerMethodField()
    suggested_note = serializers.SerializerMethodField()
    plan = serializers.SerializerMethodField()
    tries = serializers.SerializerMethodField()
    # The try a tap of "Use this one" chose (#180); null for any other message.
    use_clip: serializers.PrimaryKeyRelatedField[ProducedItem] = serializers.PrimaryKeyRelatedField(
        source="chosen_clip", read_only=True
    )

    class Meta:
        model = Message
        fields = [
            "seq",
            "role",
            "level",
            "text",
            "created_at",
            "attachments",
            "step",
            "suggested_note",
            "plan",
            "tries",
            "use_clip",
        ]

    def get_step(self, message: Message) -> dict[str, object] | None:
        """The scene step the message is about, and how it stands, or null."""
        if message.step_scene is None:
            return None
        return {
            "scene": message.step_scene,
            "kind": message.step_kind,
            "state": message.step_state,
        }

    def get_suggested_note(self, message: Message) -> dict[str, object] | None:
        """The note the message suggests for a scene's card, and its scene, or null."""
        if message.suggested_note_scene is None:
            return None
        return {"scene": message.suggested_note_scene, "text": message.suggested_note}

    def get_plan(self, message: Message) -> dict[str, object] | None:
        """The fix plan the message is about and what it says of it, or null. A notice
        showing a plan carries the plan as it stands now; the others name it."""
        plan = message.fix_plan
        if plan is None or not message.plan_event:
            return None
        shown = message.plan_event == Message.PlanEvent.SHOWN
        return {
            "id": plan.pk,
            "event": message.plan_event,
            "notice": notice(plan) if shown else None,
        }

    def get_tries(self, message: Message) -> dict[str, object] | None:
        """Every try of a clip the judge failed every try of, as it stands now, with the one
        in the ad, for a notice showing them to pick from (#180), or null."""
        if message.tries_of is None:
            return None
        shown = work.tries_shown(message.tries_of)
        return {
            "scene": shown.scene,
            "in_ad": shown.in_ad.pk if shown.in_ad is not None else None,
            "clips": [
                {
                    "clip": each.clip.pk,
                    "name": each.name,
                    "version": each.version,
                    "url": file_store.url(each.clip.file),
                    "problem": each.problem,
                }
                for each in shown.tries
            ],
        }


class SessionSerializer(serializers.ModelSerializer[Session]):
    class Meta:
        model = Session
        fields = ["id", "name", "created_at"]
        read_only_fields = ["id", "created_at"]


@api_view(["GET", "POST"])
def sessions(request: Request) -> Response:
    """List the sessions, newest first, or start a new one. A new session has no name
    until the user's first message names it."""
    if request.method == "POST":
        session = Session.objects.create()
        return Response(SessionSerializer(session).data, status=status.HTTP_201_CREATED)
    return Response(SessionSerializer(Session.objects.all(), many=True).data)


class RenameSerializer(serializers.Serializer[None]):
    name = serializers.CharField(max_length=200)


@api_view(["GET", "PATCH"])
def session(request: Request, session_id: str) -> Response:
    """The session with its whole conversation, or a name of the user's choosing for it."""
    found = get_object_or_404(Session, pk=session_id)
    if request.method == "PATCH":
        rename = RenameSerializer(data=request.data)
        rename.is_valid(raise_exception=True)
        found.name = rename.validated_data["name"]
        found.save(update_fields=["name"])
        return Response(SessionSerializer(found).data)
    history = found.messages.select_related("fix_plan").prefetch_related("attachments")
    return Response(
        {**SessionSerializer(found).data, "messages": MessageSerializer(history, many=True).data}
    )


class SendSerializer(serializers.Serializer[None]):
    """What the chat box sends: free text, photos, or both. There is no link field, no
    length field and no set of options to pick from."""

    text = serializers.CharField(
        max_length=MAX_TEXT_CHARACTERS, required=False, allow_blank=True, default=""
    )
    photos = serializers.ListField(
        child=serializers.FileField(), required=False, max_length=MAX_PHOTOS, default=list
    )
    plan = serializers.IntegerField(
        required=False,
        allow_null=True,
        default=None,
        help_text="The fix plan whose yes button sent this. Never set by typing.",
    )
    use_clip = serializers.IntegerField(
        required=False,
        allow_null=True,
        default=None,
        help_text='The try of a clip the judge failed every try of whose "Use this one" '
        "button sent this. Never set by typing.",
    )

    def validate_photos(self, photos: list[UploadedFile[bytes]]) -> list[UploadedFile[bytes]]:
        # The same formats and size the rest of the system keeps: what the models can read.
        for photo in photos:
            if photo.content_type not in IMAGE_TYPES:
                raise serializers.ValidationError(f"{photo.name} isn't a {IMAGE_TYPE_NAMES} image.")
            if (photo.size or 0) > MAX_PHOTO_BYTES:
                raise serializers.ValidationError(
                    f"{photo.name} is over {MAX_PHOTO_BYTES // 1_000_000} MB."
                )
        return photos

    def validate(self, data: dict[str, object]) -> dict[str, object]:
        if not data["text"] and not data["photos"] and data["use_clip"] is None:
            raise serializers.ValidationError({"text": "Type something, or attach a photo."})
        return data


class PollSerializer(serializers.Serializer[None]):
    after = serializers.IntegerField(min_value=0, default=0)


@api_view(["GET", "POST"])
def session_messages(request: Request, session_id: str) -> Response:
    """Send a message, or ask for the messages numbered above `?after=`. The page passes
    the last number it has seen, so a poll only gets what it hasn't shown yet."""
    found = get_object_or_404(Session, pk=session_id)
    if request.method == "POST":
        return _send(found, request)
    poll = PollSerializer(data=request.query_params)
    poll.is_valid(raise_exception=True)
    newer = (
        found.messages.filter(seq__gt=poll.validated_data["after"])
        .select_related("fix_plan")
        .prefetch_related("attachments")
    )
    return Response(MessageSerializer(newer, many=True).data)


def _send(session: Session, request: Request) -> Response:
    """Take the user's message, and start the producer's worker on it unless it is already
    working. Once the ad has a clip, the worker routes it by code first (agents/routing.py):
    to the decider, which may pass it on to the producer, or, for a tap, to the plan it says
    yes to.
    An interrupt is stored and acknowledged straight away rather than refused, and the
    working producer reads it next. Only one session works at a time, so a message to a
    session while another is busy is refused, and not kept. A tap of a plan's yes button
    names the plan, and is refused unless that plan is still waiting in this session and has
    no yes from it yet."""
    sending = SendSerializer(data=request.data)
    sending.is_valid(raise_exception=True)
    photos: list[UploadedFile[bytes]] = sending.validated_data["photos"]
    # Looked at, kept and started under one lock, so no other message can start a
    # session between the look and the start.
    with transaction.atomic():
        hold_one_at_a_time_lock()
        if another_session_is_busy(session):
            return Response({"detail": ANOTHER_SESSION_IS_BUSY}, status=status.HTTP_409_CONFLICT)
        tapped = sending.validated_data["plan"]
        if tapped is not None and (refused := _refuse_tap(session, tapped)) is not None:
            return Response({"plan": refused}, status=status.HTTP_400_BAD_REQUEST)
        chosen = None
        if sending.validated_data["use_clip"] is not None:
            try:
                chosen = _chosen_try(session, sending.validated_data["use_clip"])
            except work.TryNotOnOffer as refused_try:
                return Response({"use_clip": str(refused_try)}, status=status.HTTP_400_BAD_REQUEST)
        kept = []
        for photo in photos:
            extension = mimetypes.guess_extension(photo.content_type or "") or ""
            key = file_store.save(
                f"sessions/{session.pk}/photos/{uuid.uuid4()}{extension}", photo.read()
            )
            kept.append(messages.AttachedFile(Attachment.Kind.PICTURE, key))
        text = sending.validated_data["text"]
        if tapped is not None:
            # A tap says yes and nothing else, whatever text came with it.
            text = TAPPED_YES
        elif chosen is not None:
            assert chosen.scene is not None
            text = f"Use {work.try_name(chosen)} for scene {chosen.scene.number}."
        message = messages.add(
            session,
            role=Message.Role.USER,
            text=text,
            carrying=kept,
            plan=None if tapped is None else messages.PlanSignal(tapped, Message.PlanEvent.YES),
            chosen_clip=chosen.pk if chosen is not None else None,
        )
        # Only once the message is saved: a producer about to stop either finds it, or has
        # already stopped, and one is started here.
        wake_producer(str(session.pk))
    # The first message may just have named the session.
    session.refresh_from_db(fields=["name"])
    return Response(
        {
            "session": SessionSerializer(session).data,
            "message": MessageSerializer(message).data,
        },
        status=status.HTTP_201_CREATED,
    )


def _chosen_try(session: Session, clip: int) -> ProducedItem:
    """The try of a clip the judge failed every try of that a tap of its "Use this one"
    button in `session` chose (#180), refused, saying why, unless it can be swapped in: it
    must be a clip of this chat's ad, one of the tries the chat showed, and not the one in
    the ad already."""
    chosen = (
        ProducedItem.objects.filter(pk=clip, job__session=session, kind=ProducedItem.Kind.CLIP)
        .select_related("step", "scene")
        .first()
    )
    if chosen is None:
        raise work.TryNotOnOffer(work.TRIES_NOT_ON_OFFER)
    work.check_try_to_use(chosen)
    return chosen


def _refuse_tap(session: Session, plan: int) -> str | None:
    """Why a tap of plan `plan`'s yes button in `session` is refused, or None to keep it:
    the plan must be this chat's, still waiting, and not yet said yes to. The chat's row,
    then the plan's, stay locked until the yes is kept, so a plan replaced or settled
    meanwhile can't be said yes to. Every writer of a plan locks them in that order (the job
    first when it locks it), so two of them never wait on each other."""
    Session.objects.select_for_update().get(pk=session.pk)
    waiting = (
        FixPlan.objects.select_for_update()
        .filter(pk=plan, session=session, state=FixPlan.State.PROPOSED)
        .first()
    )
    if waiting is None:
        return PLAN_NO_LONGER_WAITS
    if waiting.messages.filter(plan_event=Message.PlanEvent.YES).exists():
        return PLAN_ALREADY_SAID_YES
    return None
