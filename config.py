"""
Inkwell — Configuration Loader
================================
Loads brain_config.json for use by app.py, query.py, and ingest.py.

brain_config.json fields:
  project_name   — display name shown in the UI
  books_dir      — path to the folder containing your PDF books
  brain_dir      — path to the folder where ChromaDB and indexes are stored
  vision_mode    — default vision mode: "skip" | "claude" | "gemini"

The setup wizard in the browser UI handles first-run configuration.
To edit manually, open brain_config.json in any text editor.
"""

import json
from pathlib import Path

CONFIG_PATH = Path(__file__).parent / "brain_config.json"


def load() -> dict:
    """
    Load and return the project configuration.
    Called by app.py, query.py, and ingest.py at startup.
    """
    if not CONFIG_PATH.exists():
        # Provide safe defaults so the app can start and show the setup wizard
        return {
            "project_name": "My Inkwell",
            "project_description": "",
            "project_goals": [],
            "books_dir": str(Path(__file__).parent / "Books"),
            "brain_dir": str(Path(__file__).parent / "knowledge_brain"),
            "vision_mode": "skip",
        }
    with open(CONFIG_PATH, encoding="utf-8") as f:
        config = json.load(f)

    # Fill in any missing fields with defaults so old configs still work
    config.setdefault("project_name",        "My Inkwell")
    config.setdefault("project_description", "")
    config.setdefault("project_goals",       [])
    config.setdefault("books_dir",  str(Path(__file__).parent / "Books"))
    config.setdefault("brain_dir",  str(Path(__file__).parent / "knowledge_brain"))
    config.setdefault("vision_mode",         "skip")

    return config