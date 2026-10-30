from django.urls import path

from . import api

urlpatterns = [
    path("sessions/", api.sessions),
    path("sessions/<uuid:session_id>/", api.session),
    path("sessions/<uuid:session_id>/messages/", api.session_messages),
    path("sessions/<uuid:session_id>/pause/", api.pause_session),
    path("sessions/<uuid:session_id>/scenes/", api.scenes),
    path("sessions/<uuid:session_id>/scenes/<int:scene>/use/", api.use_version),
]
