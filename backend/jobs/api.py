from django.core.files.uploadedfile import UploadedFile
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import serializers, status
from rest_framework.decorators import api_view
from rest_framework.request import Request
from rest_framework.response import Response

from adforge import file_store
from gateway.gateway import IMAGE_TYPE_NAMES, IMAGE_TYPES

from .activity import record
from .models import ActivityEntry, Job, ProductPhoto, Question, Scene
from .page import MAX_PHOTO_BYTES, MAX_PHOTOS
from .tasks import check_plan, keep_photo, plan_ad, read_page


class ActivityEntrySerializer(serializers.ModelSerializer[ActivityEntry]):
    class Meta:
        model = ActivityEntry
        fields = ["seq", "message", "reason", "created_at"]


class ProductPhotoSerializer(serializers.ModelSerializer[ProductPhoto]):
    url = serializers.SerializerMethodField()

    class Meta:
        model = ProductPhoto
        fields = ["position", "url", "source_url"]

    def get_url(self, photo: ProductPhoto) -> str:
        return file_store.url(photo.file)


class SceneSerializer(serializers.ModelSerializer[Scene]):
    class Meta:
        model = Scene
        fields = ["number", "line", "status"]


class QuestionSerializer(serializers.ModelSerializer[Question]):
    class Meta:
        model = Question
        fields = ["id", "kind", "question", "options"]


class JobSerializer(serializers.ModelSerializer[Job]):
    # The upper limit is the database column's, so a huge number is refused, not a crash.
    target_seconds = serializers.IntegerField(
        min_value=1, max_value=32_767, required=False, allow_null=True
    )

    class Meta:
        model = Job
        fields = ["id", "product_url", "target_seconds", "status", "created_at"]
        read_only_fields = ["id", "status", "created_at"]


@api_view(["POST"])
def start_job(request: Request) -> Response:
    serializer = JobSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    job = serializer.save()
    transaction.on_commit(lambda: read_page.delay(str(job.pk)))
    return Response(JobSerializer(job).data, status=status.HTTP_201_CREATED)


class PollSerializer(serializers.Serializer[None]):
    after = serializers.IntegerField(min_value=0, default=0)


@api_view(["GET"])
def get_job(request: Request, job_id: str) -> Response:
    """The job, its photos, and its activity entries numbered above `?after=`. The page
    passes the last number it has seen, so it only gets what changed since then."""
    poll = PollSerializer(data=request.query_params)
    poll.is_valid(raise_exception=True)
    # Read the job before its activity: a job read as finished guarantees its last
    # entry is already visible (see activity.record).
    job = get_object_or_404(Job, pk=job_id)
    activity = job.activity.filter(seq__gt=poll.validated_data["after"])
    waiting_on = job.open_question()
    return Response(
        {
            **JobSerializer(job).data,
            "photos": ProductPhotoSerializer(job.photos.all(), many=True).data,
            "scenes": SceneSerializer(job.scenes.all(), many=True).data,
            "question": QuestionSerializer(waiting_on).data if waiting_on else None,
            "activity": ActivityEntrySerializer(activity, many=True).data,
        }
    )


class ProducerAnswerSerializer(serializers.Serializer[None]):
    answer = serializers.CharField(max_length=2000)


class LineChoiceSerializer(serializers.Serializer[None]):
    answer = serializers.ChoiceField(choices=Question.LineChoice.choices)
    # The same limit as an answer. Only for "own": it is the line the scene then says.
    line = serializers.CharField(max_length=2000, required=False)

    def validate(self, data: dict[str, str]) -> dict[str, str]:
        if data["answer"] == Question.LineChoice.OWN and not data.get("line"):
            raise serializers.ValidationError({"line": "Give the line the scene should say."})
        return data


class LengthChoiceSerializer(serializers.Serializer[None]):
    answer = serializers.ChoiceField(choices=Job.LengthChoice.choices)


class WorkingLinkSerializer(serializers.Serializer[None]):
    # The same limit as a job's link.
    answer = serializers.URLField(max_length=2000)


class PhotoUploadSerializer(serializers.Serializer[None]):
    # The same limits as photos taken from a page.
    photos = serializers.ListField(
        child=serializers.FileField(), min_length=1, max_length=MAX_PHOTOS
    )

    def validate_photos(self, photos: list[UploadedFile[bytes]]) -> list[UploadedFile[bytes]]:
        for photo in photos:
            if photo.content_type not in IMAGE_TYPES:
                raise serializers.ValidationError(f"{photo.name} isn't a {IMAGE_TYPE_NAMES} image.")
            if (photo.size or 0) > MAX_PHOTO_BYTES:
                raise serializers.ValidationError(
                    f"{photo.name} is over {MAX_PHOTO_BYTES // 1_000_000} MB."
                )
        return photos


@api_view(["POST"])
def answer_question(request: Request, job_id: str) -> Response:
    """Answer the question the job is waiting on, and let the job carry on with it."""
    with transaction.atomic():
        # Locked, so two answers sent at once can't both be taken.
        job = get_object_or_404(Job.objects.select_for_update(), pk=job_id)
        question = job.open_question()
        if question is None:
            return Response(
                {"detail": "This job isn't waiting for an answer."},
                status=status.HTTP_409_CONFLICT,
            )
        _TAKE_ANSWER[Question.Kind(question.kind)](job, question, request.data)
    return Response(status=status.HTTP_202_ACCEPTED)


def _store_answer(question: Question, answer: str) -> None:
    question.answer = answer
    question.answered_at = timezone.now()
    question.save(update_fields=["answer", "answered_at"])


def _take_working_link(job: Job, question: Question, data: object) -> None:
    serializer = WorkingLinkSerializer(data=data)
    serializer.is_valid(raise_exception=True)
    link: str = serializer.validated_data["answer"]
    _store_answer(question, link)
    last_link = job.product_url
    job.product_url = link
    job.save(update_fields=["product_url"])
    record(
        job,
        f"You sent a new link: {link}",
        reason=f"The last link, {last_link}, didn't lead to one product's page, so the page "
        "is read again from this one.",
        status=Job.Status.QUEUED,
    )
    transaction.on_commit(lambda: read_page.delay(str(job.pk)))


def _take_product_photos(job: Job, question: Question, data: object) -> None:
    serializer = PhotoUploadSerializer(data=data)
    serializer.is_valid(raise_exception=True)
    photos: list[UploadedFile[bytes]] = serializer.validated_data["photos"]
    for position, photo in enumerate(photos, start=1):
        keep_photo(job, position, photo.read(), photo.content_type or "")
    _store_answer(question, "Uploaded " + ", ".join(photo.name or "a photo" for photo in photos))
    record(
        job,
        f"You uploaded {len(photos)} product photo{'s' if len(photos) != 1 else ''}",
        reason="The ad has to show the real product, so your photos are kept with the job.",
        status=Job.Status.PAGE_READ,
    )
    transaction.on_commit(lambda: plan_ad.delay(str(job.pk)))


def _take_producer_answer(job: Job, question: Question, data: object) -> None:
    serializer = ProducerAnswerSerializer(data=data)
    serializer.is_valid(raise_exception=True)
    _store_answer(question, serializer.validated_data["answer"])
    record(
        job,
        f"You answered: {question.answer}",
        reason="The ad is planned again with your answer, so nothing has to be guessed.",
        status=Job.Status.PAGE_READ,
    )
    transaction.on_commit(lambda: plan_ad.delay(str(job.pk)))


def _take_page_answer(job: Job, question: Question, data: object) -> None:
    serializer = ProducerAnswerSerializer(data=data)
    serializer.is_valid(raise_exception=True)
    _store_answer(question, serializer.validated_data["answer"])
    _check_again(
        job,
        f"You answered: {question.answer}",
        reason="The lines are checked against the page again, with your answer.",
    )


def _take_line_choice(job: Job, question: Question, data: object) -> None:
    serializer = LineChoiceSerializer(data=data)
    serializer.is_valid(raise_exception=True)
    scene = question.scene
    assert scene is not None
    if serializer.validated_data["answer"] == Question.LineChoice.KEEP:
        _store_answer(question, Question.LineChoice.KEEP.label)
        message = f"You kept scene {scene.number}'s line"
    else:
        scene.line = serializer.validated_data["line"]
        _store_answer(question, scene.line)
        message = f"You gave scene {scene.number}'s line: {scene.line}"
    # The user knows their product: the line they chose is used as it is, not checked again.
    scene.fact_checked = True
    scene.save(update_fields=["line", "fact_checked"])
    _check_again(job, message, reason="The line is used as you chose, and the checks go on.")


def _take_length_choice(job: Job, question: Question, data: object) -> None:
    serializer = LengthChoiceSerializer(data=data)
    serializer.is_valid(raise_exception=True)
    choice = Job.LengthChoice(serializer.validated_data["answer"])
    _store_answer(question, choice.label)
    job.length_choice = choice
    job.shorten_tries = 0
    job.save(update_fields=["length_choice", "shorten_tries"])
    if choice == Job.LengthChoice.SHORTEN:
        _check_again(
            job,
            "You chose to shorten the script",
            reason="The producer rewrites the script to fit, and the new lines are checked.",
        )
    else:
        _check_again(
            job,
            "You chose to keep the script longer",
            reason="The ad goes on at this length; the finished ad reports how far it is "
            "from your target.",
        )


def _check_again(job: Job, message: str, *, reason: str) -> None:
    record(job, message, reason=reason, status=Job.Status.CHECKING_PLAN)
    transaction.on_commit(lambda: check_plan.delay(str(job.pk)))


# How each kind of question's answer is taken. Every kind must be here.
_TAKE_ANSWER = {
    Question.Kind.WORKING_LINK: _take_working_link,
    Question.Kind.PRODUCT_PHOTOS: _take_product_photos,
    Question.Kind.PRODUCER: _take_producer_answer,
    Question.Kind.UNCLEAR_PAGE: _take_page_answer,
    Question.Kind.FACT_CHECK: _take_line_choice,
    Question.Kind.LENGTH: _take_length_choice,
}
