"""
Migration orchestrator.

Ties together the Flickr client, Google Photos client, EXIF writer and
metadata store to execute the full migration workflow.

Per-photo workflow
------------------
1. Fetch all metadata from Flickr and persist to disk (PENDING → metadata saved)
2. Download the highest-resolution original (DOWNLOADING → DOWNLOADED)
3. Embed EXIF metadata into the downloaded file
4. Upload the file to Google Photos (UPLOADING → UPLOADED)
5. Create / find the destination albums and add the item (ADDING_TO_ALBUM → COMPLETED)
6. Optionally delete the photo from Flickr (DELETING_FROM_FLICKR → DELETED_FROM_FLICKR)

Idempotency
-----------
If the script is restarted, photos that are already in COMPLETED or
DELETED_FROM_FLICKR state are skipped.  Photos in intermediate states are
re-processed from the last successfully recorded step.
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from .exif_writer import write_exif_metadata
from .metadata import MetadataStore, MigrationStatus, PhotoMetadata

if TYPE_CHECKING:
    from .flickr_client import FlickrClient
    from .google_photo_client import GooglePhotoClient

logger = logging.getLogger(__name__)


class Migrator:
    """Orchestrates the Flickr → Google Photos migration."""

    def __init__(
        self,
        flickr: "FlickrClient",
        gphoto: "GooglePhotoClient",
        store: MetadataStore,
        download_dir: Path,
        delete_from_flickr: bool = False,
    ) -> None:
        self.flickr = flickr
        self.gphoto = gphoto
        self.store = store
        self.download_dir = download_dir
        self.delete_from_flickr = delete_from_flickr
        self.download_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Public entry points
    # ------------------------------------------------------------------

    def fetch_all_metadata(self) -> list[str]:
        """
        Fetch the list of all Flickr photo IDs and save their metadata locally.

        Photos that are already stored locally are skipped.
        Returns the list of all photo IDs.
        """
        photo_ids = self.flickr.get_all_photo_ids()
        for pid in photo_ids:
            if self.store.exists(pid):
                logger.debug("Metadata for %s already cached, skipping.", pid)
                continue
            try:
                meta = self.flickr.build_photo_metadata(pid)
                self.store.save(meta)
                logger.debug("Saved metadata for photo %s.", pid)
            except Exception as exc:
                logger.error("Failed to fetch metadata for photo %s: %s", pid, exc)
        return photo_ids

    def migrate_all(self, photo_ids: list[str] | None = None) -> None:
        """
        Migrate all (or a subset of) photos.

        If `photo_ids` is None, processes all locally stored photos.
        """
        if photo_ids is None:
            photo_ids = self.store.all_ids()

        total = len(photo_ids)
        logger.info("Starting migration of %d photos.", total)

        for idx, pid in enumerate(photo_ids, start=1):
            photo = self.store.load(pid)
            if photo is None:
                logger.warning("No metadata found for %s, skipping.", pid)
                continue

            if photo.status in (
                MigrationStatus.COMPLETED,
                MigrationStatus.DELETED_FROM_FLICKR,
            ):
                logger.debug("[%d/%d] Photo %s already migrated, skipping.", idx, total, pid)
                continue

            logger.info("[%d/%d] Migrating photo %s ('%s')", idx, total, pid, photo.title)
            self._migrate_one(photo)

    def migrate_one_by_id(self, flickr_id: str) -> None:
        """Migrate a single photo identified by its Flickr ID."""
        photo = self.store.load(flickr_id)
        if photo is None:
            logger.info("No cached metadata for %s; fetching from Flickr…", flickr_id)
            photo = self.flickr.build_photo_metadata(flickr_id)
            self.store.save(photo)
        self._migrate_one(photo)

    # ------------------------------------------------------------------
    # Internal migration logic
    # ------------------------------------------------------------------

    def _migrate_one(self, photo: PhotoMetadata) -> None:
        try:
            local_path = self._download(photo)
            self._write_exif(local_path, photo)
            media_item = self._upload(local_path, photo)
            self._add_to_albums(media_item["id"], photo)
            if self.delete_from_flickr:
                self._delete_from_flickr(photo)
        except Exception as exc:
            photo.status = MigrationStatus.ERROR
            photo.error_message = str(exc)
            self.store.save(photo)
            logger.error("Error migrating photo %s: %s", photo.flickr_id, exc)

    def _download(self, photo: PhotoMetadata) -> Path:
        if photo.status in (
            MigrationStatus.DOWNLOADED,
            MigrationStatus.UPLOADING,
            MigrationStatus.UPLOADED,
            MigrationStatus.ADDING_TO_ALBUM,
            MigrationStatus.COMPLETED,
            MigrationStatus.DELETING_FROM_FLICKR,
            MigrationStatus.DELETED_FROM_FLICKR,
        ):
            # Already downloaded; verify file still exists
            if photo.local_path and Path(photo.local_path).exists():
                return Path(photo.local_path)

        photo.status = MigrationStatus.DOWNLOADING
        self.store.save(photo)

        local_path = self.flickr.download_photo(photo.flickr_id, self.download_dir)

        photo.local_path = str(local_path)
        photo.status = MigrationStatus.DOWNLOADED
        self.store.save(photo)
        return local_path

    @staticmethod
    def _write_exif(local_path: Path, photo: PhotoMetadata) -> None:
        write_exif_metadata(local_path, photo)

    def _upload(self, local_path: Path, photo: PhotoMetadata) -> dict:
        if photo.status in (
            MigrationStatus.UPLOADED,
            MigrationStatus.ADDING_TO_ALBUM,
            MigrationStatus.COMPLETED,
            MigrationStatus.DELETING_FROM_FLICKR,
            MigrationStatus.DELETED_FROM_FLICKR,
        ):
            if photo.google_photo_id:
                # Return a dict with the same shape as create_media_item's return value
                return {
                    "id": photo.google_photo_id,
                    "productUrl": photo.google_photo_url or "",
                }

        photo.status = MigrationStatus.UPLOADING
        self.store.save(photo)

        description = _build_description(photo)
        upload_token = self.gphoto.upload_photo(local_path, description=description)

        # Create the media item (without album – we'll add to albums separately)
        media_item = self.gphoto.create_media_item(
            upload_token=upload_token,
            filename=local_path.name,
            description=description,
        )

        photo.google_photo_id = media_item.get("id")
        photo.google_photo_url = media_item.get("productUrl")
        photo.status = MigrationStatus.UPLOADED
        self.store.save(photo)
        return media_item

    def _add_to_albums(self, media_item_id: str, photo: PhotoMetadata) -> None:
        if photo.status in (
            MigrationStatus.COMPLETED,
            MigrationStatus.DELETING_FROM_FLICKR,
            MigrationStatus.DELETED_FROM_FLICKR,
        ):
            return

        photo.status = MigrationStatus.ADDING_TO_ALBUM
        self.store.save(photo)

        google_album_ids: list[str] = []
        for album_title in photo.albums:
            try:
                album_id = self.gphoto.get_or_create_album(album_title)
                self.gphoto.add_to_album(album_id, media_item_id)
                google_album_ids.append(album_id)
                logger.debug("Added photo %s to album '%s'", photo.flickr_id, album_title)
            except Exception as exc:
                logger.warning(
                    "Could not add photo %s to album '%s': %s",
                    photo.flickr_id,
                    album_title,
                    exc,
                )

        photo.google_album_ids = google_album_ids
        photo.status = MigrationStatus.COMPLETED
        self.store.save(photo)

    def _delete_from_flickr(self, photo: PhotoMetadata) -> None:
        photo.status = MigrationStatus.DELETING_FROM_FLICKR
        self.store.save(photo)

        self.flickr.delete_photo(photo.flickr_id)

        photo.status = MigrationStatus.DELETED_FROM_FLICKR
        self.store.save(photo)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _build_description(photo: PhotoMetadata) -> str:
    """Build a description string that includes all available metadata."""
    parts = []
    if photo.description:
        parts.append(photo.description)
    if photo.date_taken:
        parts.append(f"Date taken: {photo.date_taken}")
    if photo.tags:
        parts.append(f"Tags: {', '.join(photo.tags)}")
    if photo.flickr_url:
        parts.append(f"Flickr: {photo.flickr_url}")
    if photo.comments:
        comment_lines = [
            f"  [{c.author_name}]: {c.content}" for c in photo.comments
        ]
        parts.append("Comments:\n" + "\n".join(comment_lines))
    return "\n\n".join(parts)
