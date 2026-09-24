from django.urls import path

from . import api

urlpatterns = [
    path("jobs/<uuid:job_id>/", api.get_job),
]
