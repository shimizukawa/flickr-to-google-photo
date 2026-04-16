"""Tests for the Migrator class using mocked Flickr and Google Photos clients."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from flickr_to_google_photo.metadata import (
    GpsInfo,
    MetadataStore,
    MigrationStatus,
    PhotoMetadata,
)
from flickr_to_google_photo.migrator import Migrator, _build_description


def _make_photo(flickr_id: str = "111", **kwargs) -> PhotoMetadata:
    defaults = dict(
        flickr_id=flickr_id,
        flickr_url=f"https://flickr.com/photos/x/{flickr_id}/",
        title="My Photo",
        description="A description",
        date_taken="2023-06-15 10:30:00",
        date_upload="1686820200",
        last_update="1686820200",
        tags=["nature"],
        albums=["Summer 2023"],
        album_ids=["setid1"],
        gps=GpsInfo(latitude=35.0, longitude=139.0),
        comments=[],
        original_format="jpg",
        original_secret="secret",
        owner_nsid="nsid",
        owner_realname="Real Name",
        owner_username="user",
    )
    defaults.update(kwargs)
    return PhotoMetadata(**defaults)


@pytest.fixture
def mock_flickr():
    flickr = MagicMock()
    flickr.get_all_photo_ids.return_value = ["111", "222"]
    flickr.build_photo_metadata.side_effect = lambda pid: _make_photo(pid)
    flickr.download_photo.side_effect = lambda pid, dest: dest / f"{pid}.jpg"
    return flickr


@pytest.fixture
def mock_gphoto():
    gphoto = MagicMock()
    gphoto.upload_photo.return_value = "upload_token_abc"
    gphoto.create_media_item.return_value = {
        "id": "gphoto_id_abc",
        "productUrl": "https://photos.google.com/photo/abc",
    }
    gphoto.get_or_create_album.return_value = "google_album_id_1"
    # Default: no duplicate found in Google Photos
    gphoto.find_duplicate_media_item.return_value = None
    return gphoto


@pytest.fixture
def migrator(tmp_path, mock_flickr, mock_gphoto):
    store = MetadataStore(tmp_path)
    download_dir = tmp_path / "downloads"
    download_dir.mkdir()

    # Create fake downloaded files so _download doesn't break
    for pid in ("111", "222"):
        (download_dir / f"{pid}.jpg").write_bytes(b"\xff\xd8\xff")

    return Migrator(
        flickr=mock_flickr,
        gphoto=mock_gphoto,
        store=store,
        download_dir=download_dir,
        delete_from_flickr=False,
    )


class TestFetchAllMetadata:
    def test_saves_new_photos(self, migrator, mock_flickr):
        ids = migrator.fetch_all_metadata()
        assert ids == ["111", "222"]
        assert migrator.store.exists("111")
        assert migrator.store.exists("222")

    def test_skips_existing(self, migrator, mock_flickr):
        # Pre-save photo "111"
        migrator.store.save(_make_photo("111"))
        migrator.fetch_all_metadata()
        # build_photo_metadata should only be called once (for "222")
        mock_flickr.build_photo_metadata.assert_called_once_with("222")

    def test_handles_fetch_error(self, migrator, mock_flickr):
        mock_flickr.build_photo_metadata.side_effect = Exception("API error")
        ids = migrator.fetch_all_metadata()
        # Should not raise; photos just won't be stored
        assert ids == ["111", "222"]
        assert not migrator.store.exists("111")


class TestMigrateAll:
    def test_full_migration(self, migrator, tmp_path):
        migrator.store.save(_make_photo("111"))
        migrator.store.save(_make_photo("222"))

        with patch("flickr_to_google_photo.migrator.write_exif_metadata"):
            migrator.migrate_all()

        for pid in ("111", "222"):
            photo = migrator.store.load(pid)
            assert photo is not None
            assert photo.status == MigrationStatus.COMPLETED
            assert photo.google_photo_id == "gphoto_id_abc"

    def test_skips_already_completed(self, migrator):
        migrator.store.save(
            _make_photo("111", status=MigrationStatus.COMPLETED, google_photo_id="old")
        )
        migrator.store.save(_make_photo("222"))

        with patch("flickr_to_google_photo.migrator.write_exif_metadata"):
            migrator.migrate_all()

        # Photo 111 should not be re-processed
        p111 = migrator.store.load("111")
        assert p111.google_photo_id == "old"

    def test_error_is_recorded(self, migrator, mock_gphoto):
        migrator.store.save(_make_photo("111"))
        mock_gphoto.upload_photo.side_effect = RuntimeError("Upload failed")

        with patch("flickr_to_google_photo.migrator.write_exif_metadata"):
            migrator.migrate_all(["111"])

        photo = migrator.store.load("111")
        assert photo.status == MigrationStatus.ERROR
        assert "Upload failed" in (photo.error_message or "")


class TestUploadDuplicateSkip:
    def test_skips_upload_when_duplicate_found(self, migrator, mock_gphoto):
        """If find_duplicate_media_item returns an ID, upload should be skipped."""
        mock_gphoto.find_duplicate_media_item.return_value = "existing_gphoto_id"
        migrator.store.save(_make_photo("111"))

        with patch("flickr_to_google_photo.migrator.write_exif_metadata"):
            migrator.migrate_all(["111"])

        # Upload should NOT have been called
        mock_gphoto.upload_photo.assert_not_called()
        mock_gphoto.create_media_item.assert_not_called()

        photo = migrator.store.load("111")
        assert photo.google_photo_id == "existing_gphoto_id"
        assert photo.status == MigrationStatus.COMPLETED

    def test_proceeds_with_upload_when_no_duplicate(self, migrator, mock_gphoto):
        """If find_duplicate_media_item returns None, upload proceeds normally."""
        mock_gphoto.find_duplicate_media_item.return_value = None
        migrator.store.save(_make_photo("111"))

        with patch("flickr_to_google_photo.migrator.write_exif_metadata"):
            migrator.migrate_all(["111"])

        mock_gphoto.upload_photo.assert_called_once()
        mock_gphoto.create_media_item.assert_called_once()

        photo = migrator.store.load("111")
        assert photo.google_photo_id == "gphoto_id_abc"
        assert photo.status == MigrationStatus.COMPLETED

    def test_passes_dimensions_to_duplicate_check(self, migrator, mock_gphoto):
        """find_duplicate_media_item must receive the photo's width and height."""
        mock_gphoto.find_duplicate_media_item.return_value = None
        migrator.store.save(_make_photo("111", width=3024, height=4032))

        with patch("flickr_to_google_photo.migrator.write_exif_metadata"):
            migrator.migrate_all(["111"])

        args, kwargs = mock_gphoto.find_duplicate_media_item.call_args
        # Positional or keyword: (filename, date_taken, width, height)
        call_args = list(args) + list(kwargs.values())
        assert 3024 in call_args
        assert 4032 in call_args


class TestDeleteFromFlickr:
    def test_deletes_when_flag_set(self, tmp_path, mock_flickr, mock_gphoto):
        store = MetadataStore(tmp_path)
        download_dir = tmp_path / "downloads"
        download_dir.mkdir()
        (download_dir / "111.jpg").write_bytes(b"\xff\xd8\xff")

        migrator = Migrator(
            flickr=mock_flickr,
            gphoto=mock_gphoto,
            store=store,
            download_dir=download_dir,
            delete_from_flickr=True,
        )
        store.save(_make_photo("111"))

        with patch("flickr_to_google_photo.migrator.write_exif_metadata"):
            migrator.migrate_all(["111"])

        photo = store.load("111")
        assert photo.status == MigrationStatus.DELETED_FROM_FLICKR
        mock_flickr.delete_photo.assert_called_once_with("111")

    def test_does_not_delete_when_flag_not_set(self, migrator, mock_flickr):
        migrator.store.save(_make_photo("111"))
        with patch("flickr_to_google_photo.migrator.write_exif_metadata"):
            migrator.migrate_all(["111"])
        mock_flickr.delete_photo.assert_not_called()


class TestBuildDescription:
    def test_all_fields(self):
        from flickr_to_google_photo.metadata import PhotoComment
        photo = _make_photo(
            description="Nice pic",
            date_taken="2023-01-01",
            tags=["a", "b"],
            flickr_url="https://flickr.com/photos/x/1/",
            comments=[PhotoComment("u", "Alice", "123", "Great!")],
        )
        desc = _build_description(photo)
        assert "Nice pic" in desc
        assert "Date taken: 2023-01-01" in desc
        assert "Tags: a, b" in desc
        assert "Flickr: https://flickr.com/photos/x/1/" in desc
        assert "Alice" in desc
        assert "Great!" in desc

    def test_empty_fields(self):
        photo = _make_photo(
            description="", tags=[], comments=[], flickr_url="", date_taken=None
        )
        desc = _build_description(photo)
        assert desc == ""
