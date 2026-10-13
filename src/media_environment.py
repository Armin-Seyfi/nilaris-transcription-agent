"""Make installed Homebrew media tools visible to GUI-launched subprocesses."""
import os
from pathlib import Path


def configure_media_path(environ=None):
    """Keep existing PATH precedence and append standard installation locations."""
    environ = os.environ if environ is None else environ
    entries = environ.get("PATH", os.defpath).split(os.pathsep)
    for directory in ("/opt/homebrew/bin", "/usr/local/bin"):
        if directory not in entries and Path(directory).is_dir():
            entries.append(directory)
    environ["PATH"] = os.pathsep.join(entries)
    return environ["PATH"]
