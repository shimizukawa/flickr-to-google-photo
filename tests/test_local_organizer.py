"""Tests for the LocalOrganizer class."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from flickr_to_google_photo.local_organizer import LocalOrganizer, _safe_dirname
from flickr_to_google_photo.metadata import GpsInfo, MetadataStore, PhotoMetadata, PhotoComment


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
def setup(tmp_path):
    """Return (store, download_dir, dest_dir) with a fake photo file."""
    store = MetadataStore(tmp_path / "data")
    download_dir = tmp_path / "downloads"
    download_dir.mkdir()
    dest_dir = tmp_path / "organized"
    return store, download_dir, dest_dir


class TestSafeDirname:
    def test_normal_name(self):
        assert _safe_dirname("Summer 2023") == "Summer 2023"

    def test_unsafe_chars(self):
        result = _safe_dirname('Album: "Special" / 2023')
        assert "/" not in result
        assert ":" not in result
        assert '"' not in result

    def test_empty_after_strip(self):
        assert _safe_dirname("   ") == "uncategorized"


class TestOrganizeAll:
    def test_moves_photo_to_album_dir(self, setup, tmp_path):
        store, download_dir, dest_dir = setup
        # Create a fake JPEG file
        src = download_dir / "111.jpg"
        src.write_bytes(b"\xff\xd8\xff" + b"\x00" * 50)

        photo = _make_photo("111", albums=["Summer 2023"], local_path=str(src))
        store.save(photo)

        organizer = LocalOrganizer(store=store, dest_dir=dest_dir)

        import unittest.mock as mock
        with mock.patch("flickr_to_google_photo.local_organizer.write_exif_metadata"):
            organizer.organize_all()

        # File should be moved to the album dir
        expected = dest_dir / "Summer 2023" / "111.jpg"
        assert expected.exists()
        assert not src.exists()

        # local_path should be updated in metadata
        updated = store.load("111")
        assert updated.local_path == str(expected)

    def test_copy_preserves_original(self, setup):
        store, download_dir, dest_dir = setup
        src = download_dir / "111.jpg"
        src.write_bytes(b"\xff\xd8\xff" + b"\x00" * 50)

        photo = _make_photo("111", albums=["Summer 2023"], local_path=str(src))
        store.save(photo)

        organizer = LocalOrganizer(store=store, dest_dir=dest_dir, copy=True)

        import unittest.mock as mock
        with mock.patch("flickr_to_google_photo.local_organizer.write_exif_metadata"):
            organizer.organize_all()

        expected = dest_dir / "Summer 2023" / "111.jpg"
        assert expected.exists()
        assert src.exists()  # original preserved

    def test_no_album_goes_to_uncategorized(self, setup):
        store, download_dir, dest_dir = setup
        src = download_dir / "111.jpg"
        src.write_bytes(b"\xff\xd8\xff" + b"\x00" * 50)

        photo = _make_photo("111", albums=[], local_path=str(src))
        store.save(photo)

        organizer = LocalOrganizer(store=store, dest_dir=dest_dir)

        import unittest.mock as mock
        with mock.patch("flickr_to_google_photo.local_organizer.write_exif_metadata"):
            organizer.organize_all()

        expected = dest_dir / "uncategorized" / "111.jpg"
        assert expected.exists()

    def test_multiple_albums_copies_to_each(self, setup):
        store, download_dir, dest_dir = setup
        src = download_dir / "111.jpg"
        src.write_bytes(b"\xff\xd8\xff" + b"\x00" * 50)

        photo = _make_photo("111", albums=["Album A", "Album B"], local_path=str(src))
        store.save(photo)

        organizer = LocalOrganizer(store=store, dest_dir=dest_dir)

        import unittest.mock as mock
        with mock.patch("flickr_to_google_photo.local_organizer.write_exif_metadata"):
            organizer.organize_all()

        assert (dest_dir / "Album A" / "111.jpg").exists()
        assert (dest_dir / "Album B" / "111.jpg").exists()

    def test_skips_photo_without_local_path(self, setup):
        store, download_dir, dest_dir = setup
        photo = _make_photo("111", albums=["Summer 2023"], local_path=None)
        store.save(photo)

        organizer = LocalOrganizer(store=store, dest_dir=dest_dir)
        organizer.organize_all()  # should not raise

        assert not (dest_dir / "Summer 2023").exists()

    def test_skips_missing_file(self, setup):
        store, download_dir, dest_dir = setup
        photo = _make_photo(
            "111", albums=["Summer 2023"], local_path=str(download_dir / "missing.jpg")
        )
        store.save(photo)

        organizer = LocalOrganizer(store=store, dest_dir=dest_dir)
        organizer.organize_all()  # should not raise

        assert not (dest_dir / "Summer 2023").exists()

    def test_already_in_place_no_error(self, setup):
        store, download_dir, dest_dir = setup
        # Put file already in the expected destination
        album_dir = dest_dir / "Summer 2023"
        album_dir.mkdir(parents=True)
        src = album_dir / "111.jpg"
        src.write_bytes(b"\xff\xd8\xff" + b"\x00" * 50)

        photo = _make_photo("111", albums=["Summer 2023"], local_path=str(src))
        store.save(photo)

        organizer = LocalOrganizer(store=store, dest_dir=dest_dir)

        import unittest.mock as mock
        with mock.patch("flickr_to_google_photo.local_organizer.write_exif_metadata"):
            organizer.organize_all()  # should not raise

        assert src.exists()
