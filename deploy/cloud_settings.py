"""The settings on the cloud machine: the usual ones (adforge/settings.py), with Django's
errors printed to the logs. Copied into the backend image as adforge.cloud_settings
(deploy/backend.Dockerfile)."""

from adforge.settings import *  # noqa: F403

# With DEBUG off, Django sends a failed request's error only to the site admins by email,
# and there are none, so it would be lost. Print it to the container's log instead.
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "INFO"},
    "loggers": {
        "django": {"handlers": ["console"], "level": "INFO", "propagate": False},
    },
}
