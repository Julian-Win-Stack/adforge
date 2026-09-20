import os
from pathlib import Path

import dj_database_url
import django_stubs_ext

# Lets admin and other Django classes take type parameters at runtime, for mypy.
django_stubs_ext.monkeypatch()

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "dev-only-not-secret")
DEBUG = os.environ.get("DJANGO_DEBUG", "1") == "1"
ALLOWED_HOSTS = os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1,backend").split(",")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "chat",
    "jobs",
    "gateway",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "adforge.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "adforge.wsgi.application"

DATABASES = {
    "default": dj_database_url.config(
        default="postgres://adforge:adforge@localhost:5432/adforge",
    )
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
USE_TZ = True
TIME_ZONE = "UTC"
LANGUAGE_CODE = "en-us"

STATIC_URL = "static/"
MEDIA_URL = "/media/"
MEDIA_ROOT = Path(os.environ.get("MEDIA_ROOT", BASE_DIR / "media"))

# No authentication, per the spec. The browser talks to the API through the Vite proxy.
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.AllowAny"],
    "UNAUTHENTICATED_USER": None,
}

CELERY_BROKER_URL = os.environ.get("CELERY_BROKER_URL", "redis://localhost:6379/0")
CELERY_TASK_ACKS_LATE = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
# Empty means OpenAI's own servers. Tests point it at a local stand-in.
OPENAI_BASE_URL = os.environ.get("OPENAI_BASE_URL", "")

INWORLD_API_KEY = os.environ.get("INWORLD_API_KEY", "")
# Tests point it at a local stand-in.
INWORLD_BASE_URL = os.environ.get("INWORLD_BASE_URL", "https://api.inworld.ai")

# Links and photo links that lead to this machine or a private network are never fetched,
# so a pasted link can't make the server reach places only it can see. Tests turn this on
# to fetch from their local test shop.
FETCH_PRIVATE_ADDRESSES = False

# Seconds to wait before each retry when an outside service is down or errors.
# Three tries in total: the first, then one after each delay.
RETRY_DELAYS_SECONDS = [2.0, 8.0]
