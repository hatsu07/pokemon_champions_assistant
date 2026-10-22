"""Non-secret preferences for the manual ChatGPT vision benchmark UI.

OAuth tokens are exclusively stored by ProtectedSessionStore (Windows DPAPI).
Only a model identifier is stored here; it never grants access on its own.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import tempfile

_MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")


class VisionPreferenceStore:
    def __init__(self, root: str | Path):
        self.path = Path(root) / "config" / "vision-preferences.json"

    def selected_model(self) -> str | None:
        try:
            if not self.path.is_file():
                return None
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or data.get("schema_version") != 1:
                return None
            model = data.get("selected_model")
            if isinstance(model, str) and _MODEL.fullmatch(model):
                return model
        except (OSError, ValueError, UnicodeError):
            pass
        return None

    def save_model(self, model: str) -> None:
        if not isinstance(model, str) or _MODEL.fullmatch(model) is None:
            raise ValueError("invalid model identifier")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        content = json.dumps({"schema_version": 1, "selected_model": model}, ensure_ascii=False)
        fd, temporary = tempfile.mkstemp(prefix=".vision-preference-", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
