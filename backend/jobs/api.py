from django.shortcuts import get_object_or_404
from rest_framework import serializers
from rest_framework.decorators import api_view
from rest_framework.request import Request
from rest_framework.response import Response

from adforge import file_store

from .models import Job, ProductPhoto, Scene


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


class JobSerializer(serializers.ModelSerializer[Job]):
    class Meta:
        model = Job
        fields = ["id", "product_url", "target_seconds", "status", "created_at"]


@api_view(["GET"])
def get_job(request: Request, job_id: str) -> Response:
    """The job, with its photos and its scenes. Progress and questions are told in the chat."""
    job = get_object_or_404(Job, pk=job_id)
    return Response(
        {
            **JobSerializer(job).data,
            "photos": ProductPhotoSerializer(job.photos.all(), many=True).data,
            "scenes": SceneSerializer(job.scenes.all(), many=True).data,
        }
    )
