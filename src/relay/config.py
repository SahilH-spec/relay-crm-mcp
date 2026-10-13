"""Loads and validates settings from the project's .env file."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# src/relay/config.py -> project root. Using an absolute path matters because
# Claude Desktop launches the server from its own working directory.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")

REQUIRED_KEYS = ("SF_MY_DOMAIN_URL", "SF_CLIENT_ID", "SF_CLIENT_SECRET")


class ConfigError(RuntimeError):
    """Raised when .env is missing or filled in wrong."""


@dataclass(frozen=True)
class Settings:
    sf_domain: str
    sf_client_id: str
    sf_client_secret: str
    sf_api_version: str = "62.0"

    def __repr__(self) -> str:  # never print the secret, even by accident
        return f"Settings(sf_domain={self.sf_domain!r}, sf_api_version={self.sf_api_version!r})"


def normalize_domain(raw: str) -> str:
    domain = raw.strip().rstrip("/")
    if not domain.startswith("https://"):
        domain = "https://" + domain.removeprefix("http://")
    if "lightning.force.com" in domain:
        raise ConfigError(
            "SF_MY_DOMAIN_URL is the browser address. Use the My Domain URL from "
            "Setup > My Domain (it ends in .my.salesforce.com)."
        )
    if not domain.endswith(".my.salesforce.com"):
        raise ConfigError("SF_MY_DOMAIN_URL should end in .my.salesforce.com")
    return domain


def load_settings() -> Settings:
    missing = [key for key in REQUIRED_KEYS if not os.getenv(key, "").strip()]
    if missing:
        raise ConfigError(
            f"Missing in .env: {', '.join(missing)}. "
            "Copy .env.example to .env and fill it in."
        )
    return Settings(
        sf_domain=normalize_domain(os.environ["SF_MY_DOMAIN_URL"]),
        sf_client_id=os.environ["SF_CLIENT_ID"].strip(),
        sf_client_secret=os.environ["SF_CLIENT_SECRET"].strip(),
        sf_api_version=os.getenv("SF_API_VERSION", "62.0").strip(),
    )
