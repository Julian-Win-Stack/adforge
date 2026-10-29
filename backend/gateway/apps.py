import logging
from urllib.parse import urlparse

from django.apps import AppConfig
from django.conf import settings

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
                    "AZURE_OPENAI_KEY or AZURE_OPENAI_BASE_URL is not set: a picture OpenAI "
                    "refuses for money fails, as nothing draws it instead"
                )
        else:
            logger.warning(
                "OPENAI_API_KEY is not set: every picture will fail (pictures are only drawn "
                "on OpenAI's own API); text and vision calls go to %s",
                others,
            )


def _host(address: str) -> str:
    """Where calls to this address go. An empty address means OpenAI's own servers."""
    return urlparse(address).hostname or "api.openai.com"
