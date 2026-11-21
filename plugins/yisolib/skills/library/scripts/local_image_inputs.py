"""Supply reviewed local files directly to the fixed image-proof reader."""
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch

import local_sources


@dataclass(frozen=True)
class ReviewedFile:
    path: Path
    sha256: str
    limit: int

    def stat(self):
        return local_sources.safe_source(self.path).stat()

    def read_bytes(self):
        return local_sources.read_bytes(self.path, self.limit, self.sha256)


class ReviewedInputs:
    """The historical reader addresses input by hash and suffix. No staging files are created."""
    def __init__(self, images, sources, previews):
        self.files = {key + ".bin": ReviewedFile(path, key, images.MAX_SOURCE) for key, path in sources.items()}
        self.files.update({key + ".png": ReviewedFile(path, key, images.MAX_BYTES) for key, path in previews.items()})

    def joinpath(self, name):
        if name not in self.files:
            raise ValueError(f"image proof requested a file outside the reviewed batch: {name}")
        return self.files[name]

    __truediv__ = joinpath


@contextmanager
def direct_inputs(images, database, sources, previews):
    """Keep the original ZIP, relationship, image, and asset-store validators on the write path."""
    inputs = ReviewedInputs(images, sources, previews)
    original_root, original_safe, original_fs = images.input_root, images.safe, images.fs_path

    def input_root(connection):
        main = next(row[2] for row in connection.execute("PRAGMA database_list") if row[1] == "main")
        return inputs if main and Path(main) == database else original_root(connection)

    def safe(path):
        if path is inputs or isinstance(path, ReviewedFile):
            return path
        return original_safe(path)

    def fs_path(path):
        return inputs if path is inputs else original_fs(path)

    # The batch CLI runs one writer per process. Restore all runtime functions on every exit.
    with patch.multiple(images, input_root=input_root, safe=safe, fs_path=fs_path):
        yield
