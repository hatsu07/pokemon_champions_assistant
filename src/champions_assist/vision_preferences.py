"""Non-secret ChatGPT model and reasoning settings (OAuth tokens use DPAPI)."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import tempfile
from .reasoning_config import VALID_EFFORTS

_MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\\Z")


class VisionPreferenceStore:
    def __init__(self, root: str | Path):
        self.path = Path(root) / "config" / "vision-preferences.json"

    def _read(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("schema_version") == 1:
                result = {"schema_version": 1}
                if isinstance(data.get("selected_model"), str) and _MODEL.fullmatch(data["selected_model"]):
                    result["selected_model"] = data["selected_model"]
                if isinstance(data.get("reasoning_effort"), str) and data["reasoning_effort"] in VALID_EFFORTS:
                    result["reasoning_effort"] = data["reasoning_effort"]
                return result
        except (OSError, ValueError, UnicodeError, TypeError):
            pass
        return {"schema_version": 1}

    def selected_model(self) -> str | None:
        return self._read().get("selected_model")

    def reasoning_effort(self) -> str:
        return self._read().get("reasoning_effort", "auto")

    def save_model(self, model: str) -> None:
        if not isinstance(model, str) or _MODEL.fullmatch(model) is None:
            raise ValueError("invalid model identifier")
        data = self._read()
        data["selected_model"] = model
        self._save(data)

    def save_reasoning_effort(self, effort: str) -> None:
        if not isinstance(effort, str) or effort not in VALID_EFFORTS:
            raise ValueError("invalid reasoning effort")
        data = self._read()
        data["reasoning_effort"] = effort
        self._save(data)

    def _save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        content = json.dumps(data, ensure_ascii=False)
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
