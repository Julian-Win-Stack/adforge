from django.urls import path

from . import api

urlpatterns = [
    path("sessions/", api.sessions),
    path("sessions/<uuid:session_id>/", api.session),
    path("sessions/<uuid:session_id>/messages/", api.session_messages),
]
