"""Documented stub for a future MS Graph FileSource (§8.1).

v1 deliberately does NOT use Graph. The local OneDrive sync folder is read as a plain path,
which needs no app registration, no MSAL device-code flow, no token cache, and works offline
(§8.1 table). Fill this in only if a real need to reach un-synced files appears; the cheaper fix
is usually to mark the OneDrive folder "Always keep on this device".
"""
from __future__ import annotations

from typing import BinaryIO

from .base import SourceFile


class GraphFileSource:
    def __init__(self, *args, **kwargs):
        raise NotImplementedError(
            "GraphFileSource is a v1 stub. Use LocalFolderSource against the synced OneDrive "
            "folder (see sources/graph.py docstring and impl-spec §8.1).")

    def list(self, rel_dir: str, pattern: str) -> list[SourceFile]: ...
    def open(self, f: SourceFile) -> BinaryIO: ...
    def put(self, rel_path: str, data: bytes) -> SourceFile: ...
