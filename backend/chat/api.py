import mimetypes
import uuid

from django.core.files.uploadedfile import UploadedFile
from django.shortcuts import get_object_or_404
from rest_framework import serializers, status
from rest_framework.decorators import api_view
from rest_framework.request import Request
from rest_framework.response import Response

from adforge import file_store
from gateway.gateway import IMAGE_TYPE_NAMES, IMAGE_TYPES
from jobs.page import MAX_PHOTO_BYTES, MAX_PHOTOS

from . import messages
from .models import Attachment, Message, Session

# As much as one message may say. Long enough for a brief pasted in one go.
MAX_TEXT_CHARACTERS = 10_000


class AttachmentSerializer(serializers.ModelSerializer[Attachment]):
    url = serializers.SerializerMethodField()

    class Meta:
        model = Attachment
        fields = ["position", "kind", "url"]

    def get_url(self, attachment: Attachment) -> str:
        return file_store.url(attachment.file)


class MessageSerializer(serializers.ModelSerializer[Message]):
    attachments = AttachmentSerializer(many=True, read_only=True)

    class Meta:
        model = Message
        fields = ["seq", "role", "text", "created_at", "attachments"]


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
    history = found.messages.prefetch_related("attachments")
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
        if not data["text"] and not data["photos"]:
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
    newer = found.messages.filter(seq__gt=poll.validated_data["after"]).prefetch_related(
        "attachments"
    )
    return Response(MessageSerializer(newer, many=True).data)


def _send(session: Session, request: Request) -> Response:
    """Take the user's message. It is always accepted: a message sent while the agent is
    working is stored and acknowledged straight away rather than refused."""
    sending = SendSerializer(data=request.data)
    sending.is_valid(raise_exception=True)
    photos: list[UploadedFile[bytes]] = sending.validated_data["photos"]
    kept = []
    for photo in photos:
        extension = mimetypes.guess_extension(photo.content_type or "") or ""
        key = file_store.save(
            f"sessions/{session.pk}/photos/{uuid.uuid4()}{extension}", photo.read()
        )
        kept.append(messages.AttachedFile(Attachment.Kind.PICTURE, key))
    message = messages.add(
        session, role=Message.Role.USER, text=sending.validated_data["text"], carrying=kept
    )
    # The first message may just have named the session.
    session.refresh_from_db(fields=["name"])
    return Response(
        {
            "session": SessionSerializer(session).data,
            "message": MessageSerializer(message).data,
        },
        status=status.HTTP_201_CREATED,
    )
