"""Read files from the local OneDrive sync folder as a plain filesystem path (§8.1,
clarification 1). No Graph API, no OAuth in v1."""
from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path
from typing import BinaryIO

from .base import SourceFile


class LocalFolderSource:
    def __init__(self, root: str | Path):
        self.root = Path(root)

    def _to_source_file(self, path: Path) -> SourceFile:
        data = path.read_bytes()
        st = path.stat()
        return SourceFile(
            rel_path=str(path.relative_to(self.root)),
            name=path.name,
            size=st.st_size,
            modified=datetime.fromtimestamp(st.st_mtime),
            sha256=hashlib.sha256(data).hexdigest(),
        )

    def list(self, rel_dir: str, pattern: str) -> list[SourceFile]:
        base = self.root / rel_dir
        if not base.exists():
            return []
        return sorted(
            (self._to_source_file(p) for p in base.glob(pattern) if p.is_file()),
            key=lambda f: f.rel_path,
        )

    def open(self, f: SourceFile) -> BinaryIO:
        return (self.root / f.rel_path).open("rb")

    def read_bytes(self, f: SourceFile) -> bytes:
        return (self.root / f.rel_path).read_bytes()

    def put(self, rel_path: str, data: bytes) -> SourceFile:
        dest = self.root / rel_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        return self._to_source_file(dest)
