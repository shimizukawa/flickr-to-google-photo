"""
Metadata models and JSON storage for migration state.

Each photo's metadata is stored in a JSON file inside the data directory:
    <data_dir>/photos/<flickr_id>.json

A summary index is also maintained at:
    <data_dir>/index.json
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
from enum import Enum
from pathlib import Path
from typing import Any


class MigrationStatus(str, Enum):
    """Migration state for a single photo."""

    PENDING = "pending"
    DOWNLOADING = "downloading"
    DOWNLOADED = "downloaded"
    UPLOADING = "uploading"
    UPLOADED = "uploaded"
    ADDING_TO_ALBUM = "adding_to_album"
    COMPLETED = "completed"
    DELETING_FROM_FLICKR = "deleting_from_flickr"
    DELETED_FROM_FLICKR = "deleted_from_flickr"
    ERROR = "error"


@dataclass
class GpsInfo:
    latitude: float
    longitude: float
    altitude: float | None = None


@dataclass
class PhotoComment:
    author: str
    author_name: str
    date_create: str
    content: str


@dataclass
class PhotoMetadata:
    """All known metadata for a single Flickr photo."""

    flickr_id: str
    flickr_url: str
    title: str
    description: str
    date_taken: str | None  # ISO 8601
    date_upload: str | None  # Unix timestamp string
    last_update: str | None
    tags: list[str]
    albums: list[str]  # album/photoset titles
    album_ids: list[str]  # album/photoset IDs
    gps: GpsInfo | None
    comments: list[PhotoComment]
    original_format: str  # jpg, png, etc.
    original_secret: str
    owner_nsid: str
    owner_realname: str
    owner_username: str
    # Populated after download
    local_path: str | None = None
    # Populated after Google Photos upload
    google_photo_id: str | None = None
    google_photo_url: str | None = None
    google_album_ids: list[str] = field(default_factory=list)
    # Migration state
    status: MigrationStatus = MigrationStatus.PENDING
    error_message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        if self.gps is not None:
            d["gps"] = asdict(self.gps)
        if self.comments:
            d["comments"] = [asdict(c) for c in self.comments]
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> PhotoMetadata:
        d = dict(d)  # shallow copy
        d["status"] = MigrationStatus(d.get("status", MigrationStatus.PENDING.value))
        gps_data = d.get("gps")
        d["gps"] = GpsInfo(**gps_data) if gps_data else None
        d["comments"] = [PhotoComment(**c) for c in d.get("comments", [])]
        known = {f.name for f in fields(cls)}
        d = {k: v for k, v in d.items() if k in known}
        return cls(**d)


class MetadataStore:
    """
    Persists per-photo metadata as JSON files inside `data_dir/photos/`.

    Thread-safety is *not* guaranteed; this is intended for single-process use.
    """

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.photos_dir = data_dir / "photos"
        self.photos_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, flickr_id: str) -> Path:
        return self.photos_dir / f"{flickr_id}.json"

    def save(self, photo: PhotoMetadata) -> None:
        """Persist a photo's metadata to disk."""
        path = self._path(photo.flickr_id)
        path.write_text(json.dumps(photo.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    def load(self, flickr_id: str) -> PhotoMetadata | None:
        """Load a photo's metadata from disk, or None if not found."""
        path = self._path(flickr_id)
        if not path.exists():
            return None
        return PhotoMetadata.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def exists(self, flickr_id: str) -> bool:
        return self._path(flickr_id).exists()

    def all_ids(self) -> list[str]:
        """Return all stored Flickr photo IDs."""
        return [p.stem for p in sorted(self.photos_dir.glob("*.json"))]

    def all_photos(self) -> list[PhotoMetadata]:
        """Return all stored PhotoMetadata objects."""
        result = []
        for fid in self.all_ids():
            photo = self.load(fid)
            if photo is not None:
                result.append(photo)
        return result

    def by_status(self, status: MigrationStatus) -> list[PhotoMetadata]:
        """Return photos filtered by migration status."""
        return [p for p in self.all_photos() if p.status == status]

    def summary(self) -> dict[str, int]:
        """Return a count of photos per migration status."""
        counts: dict[str, int] = {}
        for photo in self.all_photos():
            key = photo.status.value
            counts[key] = counts.get(key, 0) + 1
        return counts
