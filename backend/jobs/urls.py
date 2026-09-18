from django.urls import path

from . import api

urlpatterns = [
    path("jobs/", api.start_job),
    path("jobs/<uuid:job_id>/", api.get_job),
    path("jobs/<uuid:job_id>/answer/", api.answer_question),
]
