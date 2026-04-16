"""
Local photo organizer.

Moves (or copies) downloaded photos into album-based directory trees and
embeds EXIF metadata (including Flickr comments) into each file.

Directory layout
----------------
<dest_dir>/
    <album_title>/
        <filename>
    <uncategorized>/        ← photos with no albums
        <filename>
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from .exif_writer import write_exif_metadata
from .metadata import MetadataStore, PhotoMetadata

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

UNCATEGORIZED_DIR = "uncategorized"


class LocalOrganizer:
    """Organizes downloaded photos into album-based directories."""

    def __init__(
        self,
        store: MetadataStore,
        dest_dir: Path,
        copy: bool = False,
    ) -> None:
        self.store = store
        self.dest_dir = dest_dir
        self.copy = copy
        self.dest_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Public entry points
    # ------------------------------------------------------------------

    def organize_all(self, photo_ids: list[str] | None = None) -> None:
        """
        Organize all (or a subset of) downloaded photos.

        If `photo_ids` is None, processes all locally stored photos that
        have a valid ``local_path``.
        """
        if photo_ids is None:
            photo_ids = self.store.all_ids()

        total = len(photo_ids)
        logger.info("Organizing %d photos into %s", total, self.dest_dir)

        for idx, pid in enumerate(photo_ids, start=1):
            photo = self.store.load(pid)
            if photo is None:
                logger.warning("[%d/%d] No metadata for %s, skipping.", idx, total, pid)
                continue
            if not photo.local_path:
                logger.debug("[%d/%d] Photo %s has no local_path, skipping.", idx, total, pid)
                continue
            src = Path(photo.local_path)
            if not src.exists():
                logger.warning(
                    "[%d/%d] Local file not found for %s: %s", idx, total, pid, src
                )
                continue

            logger.info("[%d/%d] Organizing photo %s ('%s')", idx, total, pid, photo.title)
            self._organize_one(photo, src)

    def organize_one_by_id(self, flickr_id: str) -> None:
        """Organize a single photo identified by its Flickr ID."""
        photo = self.store.load(flickr_id)
        if photo is None:
            logger.error("No metadata found for photo %s.", flickr_id)
            return
        if not photo.local_path:
            logger.error("Photo %s has no local_path.", flickr_id)
            return
        src = Path(photo.local_path)
        if not src.exists():
            logger.error("Local file not found for %s: %s", flickr_id, src)
            return
        self._organize_one(photo, src)

    # ------------------------------------------------------------------
    # Internal logic
    # ------------------------------------------------------------------

    def _organize_one(self, photo: PhotoMetadata, src: Path) -> None:
        """Write EXIF (including comments) and move/copy the file into album dirs."""
        try:
            write_exif_metadata(src, photo)
        except Exception as exc:
            logger.warning("Failed to write EXIF for %s: %s", photo.flickr_id, exc)

        album_dirs = self._album_dirs(photo)

        if not album_dirs:
            album_dirs = [self.dest_dir / UNCATEGORIZED_DIR]

        # Move to first album dir; copy to any additional album dirs.
        first, *rest = album_dirs
        first.mkdir(parents=True, exist_ok=True)
        dest_path = first / src.name

        if dest_path.resolve() == src.resolve():
            logger.debug("Photo %s is already in place: %s", photo.flickr_id, dest_path)
        elif self.copy:
            shutil.copy2(src, dest_path)
            logger.debug("Copied %s → %s", src, dest_path)
        else:
            shutil.move(str(src), dest_path)
            logger.debug("Moved %s → %s", src, dest_path)
            photo.local_path = str(dest_path)
            self.store.save(photo)

        for extra_dir in rest:
            extra_dir.mkdir(parents=True, exist_ok=True)
            extra_dest = extra_dir / src.name
            shutil.copy2(dest_path, extra_dest)
            logger.debug("Copied %s → %s (extra album)", dest_path, extra_dest)

    def _album_dirs(self, photo: PhotoMetadata) -> list[Path]:
        """Return destination directories for each album the photo belongs to."""
        return [self.dest_dir / _safe_dirname(album) for album in photo.albums]


def _safe_dirname(name: str) -> str:
    """Replace filesystem-unsafe characters in album names."""
    for ch in r'\/:*?"<>|':
        name = name.replace(ch, "_")
    return name.strip() or UNCATEGORIZED_DIR
