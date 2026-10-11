"""Centralized filesystem paths for Nilaris."""

from pathlib import Path
import os


PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Migration compatibility:
# Until data migration is complete, use existing project folders.
DATA_ROOT = Path(
    os.environ.get("NILARIS_DATA_DIR", str(PROJECT_ROOT))
).expanduser().resolve()

RECORDINGS_DIR = DATA_ROOT / "recordings"
OUTPUTS_DIR = DATA_ROOT / "outputs"

# Keep logs in the project until the launcher is migrated.
LOGS_DIR = PROJECT_ROOT / "logs"
