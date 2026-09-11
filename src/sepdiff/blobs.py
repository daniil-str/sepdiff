"""Content-addressed хранилище сырого HTML: data/blobs/<sha[:2]>/<sha>.html.gz.

Статья, не менявшаяся пять изданий подряд, всё равно отдаёт каждый раз чуть
другой HTML (шаблон сайта), так что дедупликация здесь скорее страховка.
Главное — снимок сохраняется навсегда и не перекачивается никогда.
"""

from __future__ import annotations

import gzip
import os
from pathlib import Path

from .normalize import sha


class BlobStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def path(self, digest: str) -> Path:
        return self.root / digest[:2] / f"{digest}.html.gz"

    def put(self, data: bytes) -> str:
        digest = sha(data)
        dest = self.path(digest)
        if not dest.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_suffix(".tmp")
            tmp.write_bytes(gzip.compress(data, mtime=0))
            os.replace(tmp, dest)
        return digest

    def get(self, digest: str) -> bytes:
        return gzip.decompress(self.path(digest).read_bytes())
