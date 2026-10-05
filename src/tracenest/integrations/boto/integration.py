"""BotoIntegration — leverages OpenTelemetry botocore instrumentor for AWS and S3-compatible cloud SDK calls."""

import importlib
import logging
from typing import Any, Optional

from tracenest.config import SDKConfig
from tracenest.integrations.base import BaseIntegration

logger = logging.getLogger("tracenest.integrations.boto")


def _tracenest_boto_request_hook(span: Any, service: str, operation: str, params: Any) -> None:
    try:
        if span is not None and getattr(span, "is_recording", lambda: True)():
            name = getattr(span, "name", "")
            icon = "🪣" if str(service).lower() == "s3" or str(name).startswith("S3.") else "☁️"
            if name and not name.startswith("🪣") and not name.startswith("☁️"):
                if hasattr(span, "update_name"):
                    span.update_name(f"{icon} {name}")
    except Exception as exc:
        logger.debug("boto hook error", exc_info=True)


def _tracenest_boto_response_hook(span: Any, service: str, operation: str, result: Any) -> None:
    try:
        if span is not None and getattr(span, "is_recording", lambda: True)():
            name = getattr(span, "name", "")
            icon = "🪣" if str(service).lower() == "s3" or str(name).startswith("S3.") else "☁️"
            if name and not name.startswith("🪣") and not name.startswith("☁️"):
                if hasattr(span, "update_name"):
                    span.update_name(f"{icon} {name}")
    except Exception as exc:
        logger.debug("boto hook error", exc_info=True)


class BotoIntegration(BaseIntegration):
    """Deep Boto3 / Botocore instrumentation for S3 (AWS, MinIO, R2, Wasabi) and cloud services."""

    name = "boto"

    def __init__(self, config: Optional[SDKConfig] = None) -> None:
        super().__init__(config=config)

    def is_installed(self) -> bool:
        try:
            importlib.import_module("botocore")
            return True
        except ImportError:
            return False

    def _apply_patch(self) -> None:
        try:
            from opentelemetry.instrumentation.botocore import BotocoreInstrumentor

            instrumentor = BotocoreInstrumentor()
            if not instrumentor.is_instrumented_by_opentelemetry:
                instrumentor.instrument(
                    request_hook=_tracenest_boto_request_hook,
                    response_hook=_tracenest_boto_response_hook,
                )
        except Exception as exc:
            logger.debug("BotocoreInstrumentor patch skipped: %s", exc)

    def uninstrument(self) -> bool:
        try:
            from opentelemetry.instrumentation.botocore import BotocoreInstrumentor

            instrumentor = BotocoreInstrumentor()
            if instrumentor.is_instrumented_by_opentelemetry:
                instrumentor.uninstrument()
        except Exception as exc:
            logger.debug("Failed to uninstrument botocore: %s", exc)

        return super().uninstrument()
