"""File storage. Local disk today; the interface is small so an S3/GCS backend can drop in."""

import re
import uuid
from pathlib import Path

from app.core.config import get_settings

_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def safe_filename(name: str) -> str:
    name = Path(name).name  # strip any directory components
    cleaned = _SAFE.sub("_", name).strip("._") or "file"
    return cleaned[:150]


class LocalStorage:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        path = (self.root / key).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError("Invalid storage key")
        return path

    def new_key(self, prefix: str, filename: str) -> str:
        return f"{prefix}/{uuid.uuid4().hex}_{safe_filename(filename)}"

    def save(self, key: str, data: bytes) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def read(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def local_path(self, key: str) -> Path:
        return self._path(key)

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)


def get_storage() -> LocalStorage:
    return LocalStorage(get_settings().data_dir / "files")
