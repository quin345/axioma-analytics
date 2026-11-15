"""Paths for the research pipeline.

DB_PATH follows the webapp convention: the DB_PATH environment variable, or
webapp/data/axioma.db. The database is always opened read-only.
"""
import os
import pathlib

RESEARCH_DIR = pathlib.Path(__file__).resolve().parent
REPO_DIR = RESEARCH_DIR.parent
DEFAULT_DB_PATH = REPO_DIR / "webapp" / "data" / "axioma.db"
OUTPUT_DIR = pathlib.Path(os.environ.get("RESEARCH_OUTPUT_DIR", "").strip() or RESEARCH_DIR / "output")


def _dotenv_db_path():
    env = REPO_DIR / ".env"
    if not env.exists():
        return None
    for line in env.read_text().splitlines():
        key, _, value = line.partition("=")
        if key.strip() == "DB_PATH" and value.strip():
            return value.strip().strip('"').strip("'")
    return None


def db_path():
    return pathlib.Path(os.environ.get("DB_PATH", "").strip() or _dotenv_db_path() or DEFAULT_DB_PATH)
