"""Google ID tokens for calling a private Cloud Run service (the Ollama service).

On Cloud Run the metadata server signs an ID token for the instance's service
account; the receiving service checks it and that the account has
roles/run.invoker. Tokens live one hour; we cache each one and refresh it
five minutes before it expires.
"""

import threading
import time

import httpx

METADATA_URL = ("http://metadata.google.internal/computeMetadata/v1/instance/"
                "service-accounts/default/identity")
TOKEN_TTL_S = 3600
REFRESH_MARGIN_S = 300

_cache: dict[str, tuple[str, float]] = {}  # audience -> (token, fetched at)
_lock = threading.Lock()


def id_token(audience: str, now: float | None = None) -> str:
    """Return a (cached) Google ID token for `audience`.

    Args:
        audience: URL of the Cloud Run service being called, e.g.
            "https://ollama-123.asia-southeast1.run.app".
        now: Current time (tests); defaults to time.time().
    Raises:
        httpx.HTTPError: if the metadata server is unreachable (not on GCP).
    """
    now = time.time() if now is None else now
    with _lock:
        hit = _cache.get(audience)
        if hit and now - hit[1] < TOKEN_TTL_S - REFRESH_MARGIN_S:
            return hit[0]
    resp = httpx.get(METADATA_URL, params={"audience": audience},
                     headers={"Metadata-Flavor": "Google"}, timeout=5.0)
    resp.raise_for_status()
    token = resp.text.strip()
    with _lock:
        _cache[audience] = (token, now)
    return token
