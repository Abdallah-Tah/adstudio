"""Generation reliability configuration.

Defaults are conservative for hosted APIs on a Raspberry Pi coordinator. Values
can be overridden through .env without changing code.
"""
import os


def _float_env(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _int_env(name: str, default: int) -> int:
    return int(_float_env(name, float(default)))


VIDEO_SUBMISSION_TIMEOUT_S = _float_env("VIDEO_SUBMISSION_TIMEOUT_S", 30.0)
VIDEO_PROVIDER_SLA_TIMEOUT_S = _int_env("VIDEO_PROVIDER_SLA_TIMEOUT_S", 12 * 60)
VIDEO_PROVIDER_STALE_TIMEOUT_S = _int_env("VIDEO_PROVIDER_STALE_TIMEOUT_S", 3 * 60)
VIDEO_DOWNLOAD_TIMEOUT_S = _float_env("VIDEO_DOWNLOAD_TIMEOUT_S", 120.0)
VIDEO_ASSET_UPLOAD_TIMEOUT_S = _float_env("VIDEO_ASSET_UPLOAD_TIMEOUT_S", 120.0)
VIDEO_QC_TIMEOUT_S = _float_env("VIDEO_QC_TIMEOUT_S", 120.0)

VIDEO_INITIAL_POLL_DELAY_S = _int_env("VIDEO_INITIAL_POLL_DELAY_S", 15)
VIDEO_MAX_POLL_DELAY_S = _int_env("VIDEO_MAX_POLL_DELAY_S", 30)
VIDEO_INFRA_RETRY_DELAYS_S = (15, 45)

IMAGE_IDENTITY_QC_ENABLED = os.environ.get("IMAGE_IDENTITY_QC_ENABLED", "true").lower() in (
    "1", "true", "yes",
)
IMAGE_IDENTITY_QC_THRESHOLD = _float_env("IMAGE_IDENTITY_QC_THRESHOLD", 0.82)
AUTO_RETRY_IMAGES = os.environ.get("AUTO_RETRY_IMAGES", "true").lower() in (
    "1", "true", "yes",
)
SCENE_CONSISTENCY_QC_ENABLED = os.environ.get(
    "SCENE_CONSISTENCY_QC_ENABLED", "true").lower() in ("1", "true", "yes")
AUTO_RETRY_VIDEO_INFRASTRUCTURE_FAILURES = os.environ.get(
    "AUTO_RETRY_VIDEO_INFRASTRUCTURE_FAILURES", "true").lower() in ("1", "true", "yes")
AUTO_RETRY_VIDEO_QUALITY_FAILURES = os.environ.get(
    "AUTO_RETRY_VIDEO_QUALITY_FAILURES", "false").lower() in ("1", "true", "yes")
PROJECT_BUDGET_CENTS = _int_env("PROJECT_BUDGET_CENTS", 0)

FAL_WEBHOOK_URL = os.environ.get("FAL_WEBHOOK_URL", "").strip() or None
FAL_WEBHOOK_VERIFY = os.environ.get("FAL_WEBHOOK_VERIFY", "").lower() in (
    "1", "true", "yes",
)
