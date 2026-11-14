"""Application settings, loaded from environment variables / `.env`.

Every tunable that is not a physical model parameter lives here so the
rest of the code never reads `os.environ` directly.
"""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """Typed runtime configuration.

    Attributes:
        ollama_api_key: Bearer token for Ollama Cloud (OLLAMA_AUTH=key only).
        llm_provider: "ollama", "rules" (no LLM) or "fake" (tests).
        llm_model: Model name passed to the provider.
        ollama_host: Base URL of the Ollama API (our Cloud Run Ollama service
            in production, http://localhost:11434 for a local server).
        ollama_auth: "gcp" (Google ID token, private Cloud Run service),
            "key" (Bearer OLLAMA_API_KEY) or "none".
        llm_timeout_s: HTTP timeout per LLM call; long enough for a cold start.
        llm_think: Reasoning mode for thinking models (None = model default).
        sim_seed: Fixed random seed used by every simulation RNG.
        max_workers: Max concurrent per-session worker processes.
        worker_idle_timeout_s: Idle seconds before a worker process exits.
        run_ttl_h: Hours after its last write when a finished run is deleted.
        run_sweep_interval_h: Hours between two sweeps for stale runs (the
            first sweep runs at start-up); 0 disables the sweeper.
        data_dir: Root folder for caches, run frames and sqlite files.
        cors_origins: Comma separated list of allowed browser origins (dev
            servers only; in production the frontend is served same-origin).
        static_dir: Built frontend (index.html + assets) served at "/" if present.
        seed_dir: Areas shipped with the app (graphs + geocoder rows), see geo/seed.py.
        max_half_side_m: Area cap, half the side of the square bbox (m);
            707 m gives the 2 km² maximum area.
        default_half_side_m: Area half-side used when geocoding a point.
        nominatim_url: Nominatim search endpoint.
        user_agent: Generic client name sent to OSM services.
        overpass_urls: Comma separated Overpass API base URLs, tried in order.
        overpass_connect_s: TCP connect timeout per Overpass server (s); an
            unreachable server is skipped after this long.
        overpass_read_s: Max wait for one Overpass answer (s).
        overpass_budget_s: Total time allowed for one road-network download
            across all servers and retries (s).
        overpass_budget_with_fallback_s: Shorter Overpass budget used when an
            offline extract covers the area and can take over (s).
        osm_extracts_dir: Folder of roads-only Geofabrik extracts
            (`<id>.osm.pbf` + `<id>.poly` [+ `<id>.date`]); None disables the
            offline fallback. On Cloud Run it is the mounted extracts bucket.
    """

    model_config = SettingsConfigDict(
        env_file=(_REPO_ROOT / ".env", ".env"), extra="ignore"
    )

    ollama_api_key: str = ""
    llm_provider: str = "ollama"
    llm_model: str = "qwen3:8b"
    ollama_host: str = "http://localhost:11434"
    ollama_auth: str = "none"
    llm_timeout_s: float = 120.0
    llm_think: bool | None = False

    sim_seed: int = 42
    max_workers: int = 8
    worker_idle_timeout_s: int = 600
    run_ttl_h: float = 24.0
    run_sweep_interval_h: float = 24.0
    data_dir: Path = _REPO_ROOT / "data"
    cors_origins: str = "http://localhost:5173"
    static_dir: Path = _REPO_ROOT / "frontend" / "dist"
    seed_dir: Path = _REPO_ROOT / "backend" / "seed"

    max_half_side_m: float = 707.0
    default_half_side_m: float = 700.0
    nominatim_url: str = "https://nominatim.openstreetmap.org/search"
    user_agent: str = "vehicle-flow-sim/0.1"

    overpass_urls: str = (
        "https://overpass-api.de/api,"
        "https://lz4.overpass-api.de/api,"
        "https://maps.mail.ru/osm/tools/overpass/api,"
        "https://overpass.private.coffee/api"
    )
    overpass_connect_s: float = 5.0
    overpass_read_s: float = 120.0
    overpass_budget_s: float = 240.0
    overpass_budget_with_fallback_s: float = 60.0
    osm_extracts_dir: Path | None = None

    @property
    def cors_origin_list(self) -> list[str]:
        """Return CORS origins as a list."""
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def overpass_url_list(self) -> list[str]:
        """Return Overpass base URLs as a list."""
        return [u.strip().rstrip("/") for u in self.overpass_urls.split(",") if u.strip()]

    def path(self, *parts: str) -> Path:
        """Return a path under `data_dir`, creating parent folders.

        Args:
            *parts: Path components relative to the data dir.
        Returns:
            Absolute path; its parent directory exists.
        """
        p = Path(self.data_dir).joinpath(*parts)
        p.parent.mkdir(parents=True, exist_ok=True)
        return p


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide cached Settings instance."""
    return Settings()
