"""Configuration loader: .env and YAML configs."""
from pathlib import Path
import os
import yaml
from dotenv import load_dotenv

BASE_DIR = Path(__file__).parent.parent

# Load .env on import (safe to call multiple times)
load_dotenv(BASE_DIR / ".env")


def load_config() -> dict:
    """Load sources.yaml and categories.yaml. Return dict with both configs."""
    sources_path = BASE_DIR / "sources.yaml"
    categories_path = BASE_DIR / "categories.yaml"

    with sources_path.open("r", encoding="utf-8") as f:
        sources_config = yaml.safe_load(f)

    with categories_path.open("r", encoding="utf-8") as f:
        categories_config = yaml.safe_load(f)

    return {
        "sources": sources_config,
        "categories": categories_config,
    }


def get_env(key: str, required: bool = True) -> str:
    """Get an environment variable by key.

    Raises EnvironmentError if required and not set.
    Never logs the value — only the key name.
    """
    value = os.environ.get(key)
    if required and not value:
        raise EnvironmentError(
            f"Required environment variable '{key}' is not set. "
            f"Copy .env.example to .env and fill in the values."
        )
    return value or ""
