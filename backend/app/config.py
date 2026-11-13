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
        ollama_api_key: Bearer token for Ollama Cloud.
        llm_provider: "ollama" (Ollama Cloud), "rules" (no LLM) or "fake" (tests).
        llm_model: Model name passed to the provider.
        ollama_host: Base URL of the Ollama API.
        sim_seed: Fixed random seed used by every simulation RNG.
        max_workers: Max concurrent per-session worker processes.
        worker_idle_timeout_s: Idle seconds before a worker process exits.
        data_dir: Root folder for caches, run frames and sqlite files.
        cors_origins: Comma separated list of allowed browser origins.
        max_half_side_m: Area cap, half the side of the square bbox (m).
        default_half_side_m: Area half-side used when geocoding a point.
        nominatim_url: Nominatim search endpoint.
        user_agent: Generic client name sent to OSM services.
    """

    model_config = SettingsConfigDict(
        env_file=(_REPO_ROOT / ".env", ".env"), extra="ignore"
    )

    ollama_api_key: str = ""
    llm_provider: str = "ollama"
    llm_model: str = "glm-5.3-flash"
    ollama_host: str = "https://ollama.com"

    sim_seed: int = 42
    max_workers: int = 8
    worker_idle_timeout_s: int = 600
    data_dir: Path = _REPO_ROOT / "data"
    cors_origins: str = "http://localhost:5173"

    max_half_side_m: float = 1750.0
    default_half_side_m: float = 700.0
    nominatim_url: str = "https://nominatim.openstreetmap.org/search"
    user_agent: str = "vehicle-flow-sim/0.1"

    @property
    def cors_origin_list(self) -> list[str]:
        """Return CORS origins as a list."""
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

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
