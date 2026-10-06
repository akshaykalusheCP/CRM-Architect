import io
import zipfile
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from app.domain.business_model import BusinessModel


@dataclass
class ExportArtifact:
    files: dict[str, str | bytes] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    manual_steps: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)

    def to_zip(self, root: str) -> bytes:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for path, content in sorted(self.files.items()):
                data = content.encode("utf-8") if isinstance(content, str) else content
                zf.writestr(f"{root}/{path}", data)
        return buf.getvalue()

    def report(self) -> dict[str, Any]:
        return {"warnings": self.warnings, "manual_steps": self.manual_steps, **self.details}


class PlatformAdapter(ABC):
    key: str
    label: str
    description: str
    # Told to the AI when it designs automations, so it prefers what this platform can build automatically.
    automation_guidance: str | None = None

    @abstractmethod
    def generate(self, model: BusinessModel, project_name: str) -> ExportArtifact: ...

    def automation_status(self, model: BusinessModel) -> dict[str, dict]:
        """Per automation key: how the build will realise it ({"status": "flow"|"partial"|"manual", "note": str})."""
        return {}
