from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _load_dotenv(project_root: Path) -> None:
    """Load .env from project root if python-dotenv is available."""
    env_file = project_root / ".env"
    if not env_file.is_file():
        return
    try:
        from dotenv import load_dotenv  # type: ignore[import]
        load_dotenv(env_file, override=False)
    except ModuleNotFoundError:
        pass


@dataclass(frozen=True)
class SinopacCredentials:
    api_key: str
    secret_key: str
    simulation: bool
    ca_path: Path | None
    ca_passwd: str | None
    person_id: str | None

    @classmethod
    def from_env(cls, project_root: Path | None = None) -> "SinopacCredentials":
        """Read credentials from environment variables (loads .env if present)."""
        root = project_root or Path(__file__).resolve().parents[3]
        _load_dotenv(root)

        api_key = os.environ.get("SINOPAC_API_KEY", "").strip()
        secret_key = os.environ.get("SINOPAC_SECRET_KEY", "").strip()

        missing = [k for k, v in [("SINOPAC_API_KEY", api_key), ("SINOPAC_SECRET_KEY", secret_key)] if not v]
        if missing:
            raise EnvironmentError(
                f"Missing required env vars: {', '.join(missing)}\n"
                "Copy .env.example → .env and fill in your Sinopac API credentials."
            )

        simulation_raw = os.environ.get("SINOPAC_SIMULATION", "true").strip().lower()
        simulation = simulation_raw not in ("false", "0", "no")

        ca_path_raw = os.environ.get("SINOPAC_CA_PATH", "").strip()
        ca_path: Path | None = None
        if ca_path_raw:
            p = Path(ca_path_raw)
            if not p.is_absolute():
                p = (root / p).resolve()
            if not p.is_file():
                raise FileNotFoundError(f"CA certificate not found: {p}")
            ca_path = p

        ca_passwd = os.environ.get("SINOPAC_CA_PASSWD", "").strip() or None
        person_id = os.environ.get("SINOPAC_PERSON_ID", "").strip() or None

        if not simulation and ca_path is None:
            raise EnvironmentError(
                "Live trading requires SINOPAC_CA_PATH, SINOPAC_CA_PASSWD, and SINOPAC_PERSON_ID."
            )

        return cls(
            api_key=api_key,
            secret_key=secret_key,
            simulation=simulation,
            ca_path=ca_path,
            ca_passwd=ca_passwd,
            person_id=person_id,
        )

    def __repr__(self) -> str:
        mode = "simulation" if self.simulation else "LIVE"
        ca = str(self.ca_path) if self.ca_path else "none"
        return f"SinopacCredentials(mode={mode}, ca={ca}, api_key=***)"
