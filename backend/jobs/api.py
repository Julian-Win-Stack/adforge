from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework import serializers, status
from rest_framework.decorators import api_view
from rest_framework.request import Request
from rest_framework.response import Response

from adforge import file_store

from .models import ActivityEntry, Job, ProductPhoto
from .tasks import read_page


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
    return Response(
        {
            **JobSerializer(job).data,
            "photos": ProductPhotoSerializer(job.photos.all(), many=True).data,
            "activity": ActivityEntrySerializer(activity, many=True).data,
        }
    )
