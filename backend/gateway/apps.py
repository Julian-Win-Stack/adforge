import logging
import os
from urllib.parse import urlparse

from django.apps import AppConfig
from django.conf import settings

from adforge import tracing

logger = logging.getLogger(__name__)


class GatewayConfig(AppConfig):
    name = "gateway"

    def ready(self) -> None:
        # Said as a warning so it shows without any logging set up: which servers answer is
        # never left to guess (no silent fallbacks).
        others = _host(settings.OPENAI_BASE_URL)
        if settings.OPENAI_PICTURES_KEY:
            pictures = _host(settings.OPENAI_PICTURES_BASE_URL)
            logger.warning(
                "Pictures are drawn on %s; text and vision calls go to %s", pictures, others
            )
            if settings.AZURE_PICTURES_KEY:
                logger.warning(
                    "Once OpenAI refuses a picture for money, that session's pictures are "
                    "drawn on %s (deployment %s), at most %s a minute",
                    _host(settings.AZURE_PICTURES_BASE_URL),
                    settings.AZURE_PICTURES_DEPLOYMENT or "named as the model",
                    settings.AZURE_PICTURES_PER_MINUTE,
                )
            else:
                logger.warning(
                    "AZUREE_OPENAI_KEY or AZUREE_OPENAI_BASE_URL is not set: a picture OpenAI "
                    "refuses for money fails, as nothing draws it instead"
                )
        else:
            logger.warning(
                "OPENAI_API_KEY is not set: every picture will fail (pictures are only drawn "
                "on OpenAI's own API); text and vision calls go to %s",
                others,
            )
        if settings.AZURE_SECOND_KEY:
            logger.warning(
                "Calls to %s go to %s, the second Azure account",
                ", ".join(sorted(settings.AZURE_SECOND_MODELS)),
                _host(settings.AZURE_SECOND_BASE_URL),
            )
        elif any(os.environ.get(name) for name in _SECOND_AZURE_SETTINGS):
            logger.warning(
                "Not all of %s are set, so every model stays on the main account",
                ", ".join(_SECOND_AZURE_SETTINGS),
            )
        tracing.say_if_off()


_SECOND_AZURE_SETTINGS = (
    "AZURE_OPENAI_SECOND_KEY",
    "AZURE_OPENAI_SECOND_BASE_URL",
    "AZURE_OPENAI_SECOND_MODELS",
)


def _host(address: str) -> str:
    """Where calls to this address go. An empty address means OpenAI's own servers."""
    return urlparse(address).hostname or "api.openai.com"
