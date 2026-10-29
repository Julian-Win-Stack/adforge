import os
from collections.abc import Mapping
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
    "agents",
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

# How often a working producer says it is still alive, and how long it can go unseen before
# it is taken for dead and started again.
# Quality checks (CONTEXT.md) look at something already produced, such as a clip. On unless
# QUALITY_CHECKS=0; it switches off the clip judge (and its remakes), the clip judge's share
# of clip cost estimates, and the fix check.
# Planning checks always run, and so does the B-roll starting picture check, which nothing
# switches off (Julian 15 Oct 14:53: "hardcode the check in the app to always turn it on").
QUALITY_CHECKS = os.environ.get("QUALITY_CHECKS", "1") == "1"

PRODUCER_HEARTBEAT_SECONDS = 30.0
PRODUCER_DEAD_AFTER_SECONDS = 120.0
# The same for a scene step. One that has died no longer keeps its session busy, so another
# session may start.
STEP_HEARTBEAT_SECONDS = 30.0
STEP_DEAD_AFTER_SECONDS = 120.0
# How long a step a worker took can go unseen before it is failed, so its scene can be made
# again: its worker was killed. Its heartbeat beats on a thread of its own, so a long paid
# render keeps it seen (Julian 2026-10-22, 5 minutes).
STEP_FAILED_AFTER_SECONDS = 300.0
CELERY_BEAT_SCHEDULE = {
    "restart-dead-producers": {
        "task": "agents.tasks.restart_dead_producers",
        "schedule": 60.0,
    },
    "fail-dead-steps": {
        "task": "agents.tasks.fail_dead_steps",
        "schedule": 60.0,
    },
}


def openai_account(environ: Mapping[str, str]) -> tuple[str, str]:
    """The key and address for OpenAI's models. Our Azure copy of them, which serves the same
    model names, wins when both its key and its address are set. An empty address means
    OpenAI's own servers."""
    azure_key = environ.get("AZURE_OPENAI_KEY", "")
    azure_address = environ.get("AZURE_OPENAI_BASE_URL", "")
    if azure_key and azure_address:
        return azure_key, azure_address
    return environ.get("OPENAI_API_KEY", ""), environ.get("OPENAI_BASE_URL", "")


def openai_pictures_account(environ: Mapping[str, str]) -> tuple[str, str]:
    """The key and address pictures are drawn with: always OpenAI's own servers, never our
    Azure copy, even when Azure answers every other call (Julian 2026-10-20: "always use
    OpenAI API for image generation"). An empty key fails each picture, saying so."""
    return environ.get("OPENAI_API_KEY", ""), environ.get("OPENAI_BASE_URL", "")


def azure_pictures_account(environ: Mapping[str, str]) -> tuple[str, str]:
    """The key and address of our Azure copy of the picture model, where a session's pictures
    are drawn once OpenAI refuses one for money (Julian 2026-10-29: "fall back to azure with
    azure limits if openai hit the limit"). Empty unless both are set: then nothing draws
    instead, and the refused picture fails, saying so."""
    azure_key = environ.get("AZURE_OPENAI_KEY", "")
    azure_address = environ.get("AZURE_OPENAI_BASE_URL", "")
    if azure_key and azure_address:
        return azure_key, azure_address
    return "", ""


# Tests point the addresses at a local stand-in.
OPENAI_API_KEY, OPENAI_BASE_URL = openai_account(os.environ)
OPENAI_PICTURES_KEY, OPENAI_PICTURES_BASE_URL = openai_pictures_account(os.environ)
AZURE_PICTURES_KEY, AZURE_PICTURES_BASE_URL = azure_pictures_account(os.environ)
# The name of the picture model's deployment on Azure. Empty means the deployment has the
# model's own name, as Azure's copies of the other models do.
AZURE_PICTURES_DEPLOYMENT = os.environ.get("AZURE_OPENAI_PICTURES_DEPLOYMENT", "")
# How many pictures Azure's deployment draws a minute; pictures drawn there are paced to it.
AZURE_PICTURES_PER_MINUTE = 2
# How much of its reasoning an agent's model is asked to summarise on each turn, kept on the
# turn's record and its trace (spec #247 point 16): "auto", "concise" or "detailed". Empty
# asks for none, for a deployment that refuses it.
OPENAI_REASONING_SUMMARY = os.environ.get("OPENAI_REASONING_SUMMARY", "auto")

# Firecrawl reads product pages in a real browser. Empty means the page is read with a plain
# download instead, and the user is told so.
FIRECRAWL_API_KEY = os.environ.get("FIRECRAWL_API_KEY", "")
# Tests point it at a local stand-in.
FIRECRAWL_URL = os.environ.get("FIRECRAWL_URL", "https://api.firecrawl.dev")

INWORLD_API_KEY = os.environ.get("INWORLD_API_KEY", "")
# Tests point it at a local stand-in.
INWORLD_BASE_URL = os.environ.get("INWORLD_BASE_URL", "https://api.inworld.ai")

ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY", "")
# Tests point it at a local stand-in.
ELEVENLABS_BASE_URL = os.environ.get("ELEVENLABS_BASE_URL", "https://api.elevenlabs.io")

FAL_KEY = os.environ.get("FAL_KEY", "")
# Tests point it at a local stand-in.
FAL_BASE_URL = os.environ.get("FAL_BASE_URL", "https://fal.run")
# fal's queue, for clips: asked for, then waited for. Tests point it at a local stand-in.
FAL_QUEUE_URL = os.environ.get("FAL_QUEUE_URL", "https://queue.fal.run")
# fal's storage, where a B-roll clip's pictures are put for Creatify to fetch. Tests point it
# at a local stand-in.
FAL_STORAGE_URL = os.environ.get("FAL_STORAGE_URL", "https://rest.fal.ai")
# fal answers once the music is made, which takes longer the longer the music.
FAL_TIMEOUT_SECONDS = 300.0

HEYGEN_API_KEY = os.environ.get("HEYGEN_API_KEY", "")
# Where talking clips are made. Tests point it at a local stand-in.
HEYGEN_BASE_URL = os.environ.get("HEYGEN_BASE_URL", "https://api.heygen.com")
# How long to wait for HeyGen to answer one request.
HEYGEN_TIMEOUT_SECONDS = 120.0

CREATIFY_API_ID = os.environ.get("CREATIFY_API_ID", "")
CREATIFY_API_KEY = os.environ.get("CREATIFY_API_KEY", "")
# Where B-roll clips are made, by Boreal-H3. Tests point it at a local stand-in.
CREATIFY_BASE_URL = os.environ.get("CREATIFY_BASE_URL", "https://api.creatify.ai")
# How long to wait for Creatify to answer one request.
CREATIFY_TIMEOUT_SECONDS = 120.0
# "auto" has Creatify rewrite each B-roll prompt the way Boreal-H3 was evaluated with;
# "none" sends it as written.
CREATIFY_PROMPT_ENHANCEMENT = os.environ.get("CREATIFY_PROMPT_ENHANCEMENT", "auto")
# How many pieces of page videos one B-roll clip is sent at most, as Video 1, 2, 3. One is
# the normal case; more is proposed only (spec #210 audit item 7, not approved yet).
BROLL_MOST_PIECES = 1

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
# Where videos are watched. Tests point it at a local stand-in.
GEMINI_BASE_URL = os.environ.get("GEMINI_BASE_URL", "https://generativelanguage.googleapis.com")
# A request holding its video inline must stay under 20 MB in all (Gemini's video
# understanding page, "Pass video data inline"); a video that would make it bigger is put
# in Gemini's file store first and named by its link.
GEMINI_INLINE_REQUEST_BYTES = 20_000_000
# How often to ask whether a video put in Gemini's file store is ready to watch, and for
# how long before it counts as Gemini being down.
GEMINI_FILE_POLL_SECONDS = 2.0
GEMINI_FILE_WAIT_SECONDS = 300.0
# How sharply Gemini looks at each frame, and how hard it thinks: as in the watcher test
# (runs/watcher-test/run.py, Oct 27 2026).
GEMINI_MEDIA_RESOLUTION = "MEDIA_RESOLUTION_HIGH"
GEMINI_THINKING_LEVEL = "HIGH"
# A video is watched cut to this many px on its short side, never made bigger, with its
# sound, looked at this many frames a second (spec #210 point 3, as in the watcher test).
WATCH_VIDEO_SHORT_SIDE = 480
WATCH_VIDEO_FRAMES_PER_SECOND = 1.0
# A page video longer than this many seconds is not watched, and the owner is told which
# (requirement 23, Julian Oct 27 2026 04:28: "ok. 3 min"). Length is the only cut: no byte cap.
WATCH_VIDEO_MOST_SECONDS = 180
# The planner sees the library on sheets of this many pictures across and down, each numbered
# and labelled under it (#210 point 9): 2 puts 4 on a sheet, so its 50 pictures hold 200.
LIBRARY_SHEET_ACROSS = 2

# Langfuse, where every agent turn, tool call and model call is sent to be looked at as a
# tree. Tracing is off unless both keys are set.
LANGFUSE_PUBLIC_KEY = os.environ.get("LANGFUSE_PUBLIC_KEY", "")
LANGFUSE_SECRET_KEY = os.environ.get("LANGFUSE_SECRET_KEY", "")
LANGFUSE_BASE_URL = os.environ.get("LANGFUSE_BASE_URL", "https://cloud.langfuse.com")
# Which machine's traces they are, so Langfuse keeps each box's apart, such as "azure" on the
# Azure box: lowercase letters, digits, "-" and "_", not starting with "langfuse".
LANGFUSE_ENVIRONMENT = os.environ.get("LANGFUSE_ENVIRONMENT", "development")

# How often to ask whether a clip is made yet. One usually takes under a minute, but it is
# paid for, so it is waited for however long it takes: past this, the shop owner is told
# it's still being made.
CLIP_POLL_SECONDS = 10.0
CLIP_SLOW_AFTER_SECONDS = 600.0

# ffmpeg must be a build that can draw text and subtitles (drawtext, subtitles). Homebrew's
# plain ffmpeg can't, so on a Mac its ffmpeg-full is used where it's installed.
_FFMPEG_FULL = Path("/opt/homebrew/opt/ffmpeg-full/bin")
_FFMPEG_DIR = f"{_FFMPEG_FULL}/" if _FFMPEG_FULL.is_dir() else ""
FFMPEG = os.environ.get("FFMPEG", f"{_FFMPEG_DIR}ffmpeg")
FFPROBE = os.environ.get("FFPROBE", f"{_FFMPEG_DIR}ffprobe")

# Links and photo links that lead to this machine or a private network are never fetched,
# so a pasted link can't make the server reach places only it can see. Tests turn this on
# to fetch from their local test shop.
FETCH_PRIVATE_ADDRESSES = False

# Seconds to wait before each retry when an outside service is down or errors.
# Three tries in total: the first, then one after each delay.
RETRY_DELAYS_SECONDS = [2.0, 8.0]

# The producer's context (spec #247 point 15): its conversation is given in full until its
# last turn's input tokens, as the model reported them, pass this share of its model's window
# (gateway.catalog.max_input_tokens); then the oldest FOLD_SHARE_OF_CALLS of the tool calls
# still given in full fold to one line each (Julian 2026-10-29: "make it half of the window").
FOLD_PAST_SHARE_OF_WINDOW = 0.5
FOLD_SHARE_OF_CALLS = 0.5
# The most input tokens our Azure deployment of the agents' models takes in one call, when it
# is lower than the model's own window. 0 means the model's own.
AZURE_OPENAI_MAX_INPUT_TOKENS = int(os.environ.get("AZURE_OPENAI_MAX_INPUT_TOKENS") or 0)

# How many tools the agents together may call for one message from the user, so an agent
# that loops can't run up a bill. The one past it is refused, and the user is told.
MAX_TOOL_CALLS_PER_MESSAGE = 100

# The fallback of fix redesign decision 5: the route call (jobs/reference_photos.py
# where_it_goes) answers again, after the decider, where each owner photo's thing goes. Off:
# the decider's answers stand. On only if the replay test shows the decider worse at it.
ROUTE_OWNER_PHOTOS_AFTER_THE_DECIDER = False
