"""FileSource interface (§8.1). LocalFolderSource is the v1 implementation; a Graph adapter
stays possible behind the same Protocol."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import BinaryIO, Protocol


@dataclass(frozen=True)
class SourceFile:
    rel_path: str
    name: str
    size: int
    modified: datetime
    sha256: str          # computed on read; recorded in runs/bank_transactions


class FileSource(Protocol):
    def list(self, rel_dir: str, pattern: str) -> list[SourceFile]: ...
    def open(self, f: SourceFile) -> BinaryIO: ...
    def put(self, rel_path: str, data: bytes) -> SourceFile: ...
