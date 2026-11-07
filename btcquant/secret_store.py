"""Best-effort local file protection for API credentials on Windows.

This is not encryption.  It removes inherited ACL entries and grants the
current Windows user full control, so another local account cannot casually
read the JSON file.  The file still contains plaintext to the owning user.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess


def restrict_file(path: Path) -> None:
    if os.name != "nt":
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        return
    user = os.environ.get("USERNAME", "").strip()
    if not user:
        return
    try:
        subprocess.run(
            ["icacls", str(path), "/inheritance:r", "/grant:r", f"{user}:F"],
            check=False,
            capture_output=True,
            timeout=8,
        )
    except (OSError, subprocess.SubprocessError):
        pass
