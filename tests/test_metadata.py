"""Tests for metadata models and MetadataStore."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from flickr_to_google_photo.metadata import (
    GpsInfo,
    MetadataStore,
    MigrationStatus,
    PhotoComment,
    PhotoMetadata,
)


def _make_photo(flickr_id: str = "123456", **kwargs) -> PhotoMetadata:
    defaults = dict(
        flickr_id=flickr_id,
        flickr_url=f"https://www.flickr.com/photos/user/{flickr_id}/",
        title="Test Photo",
        description="A test photo",
        date_taken="2023-06-15 10:30:00",
        date_upload="1686820200",
        last_update="1686820200",
        tags=["test", "photo"],
        albums=["Vacation"],
        album_ids=["album1"],
        gps=GpsInfo(latitude=35.6812, longitude=139.7671, altitude=10.0),
        comments=[
            PhotoComment(
                author="user1",
                author_name="Alice",
                date_create="1686820300",
                content="Nice shot!",
            )
        ],
        original_format="jpg",
        original_secret="abc123",
        owner_nsid="nsid@N00",
        owner_realname="Test User",
        owner_username="testuser",
    )
    defaults.update(kwargs)
    return PhotoMetadata(**defaults)


class TestPhotoMetadata:
    def test_to_dict_roundtrip(self):
        photo = _make_photo()
        d = photo.to_dict()
        restored = PhotoMetadata.from_dict(d)

        assert restored.flickr_id == photo.flickr_id
        assert restored.title == photo.title
        assert restored.status == MigrationStatus.PENDING
        assert restored.gps is not None
        assert restored.gps.latitude == pytest.approx(35.6812)
        assert len(restored.comments) == 1
        assert restored.comments[0].content == "Nice shot!"

    def test_status_serialisation(self):
        photo = _make_photo(status=MigrationStatus.COMPLETED)
        d = photo.to_dict()
        assert d["status"] == "completed"
        restored = PhotoMetadata.from_dict(d)
        assert restored.status == MigrationStatus.COMPLETED

    def test_none_gps(self):
        photo = _make_photo(gps=None)
        d = photo.to_dict()
        assert d["gps"] is None
        restored = PhotoMetadata.from_dict(d)
        assert restored.gps is None

    def test_empty_comments(self):
        photo = _make_photo(comments=[])
        d = photo.to_dict()
        restored = PhotoMetadata.from_dict(d)
        assert restored.comments == []

    def test_default_status_is_pending(self):
        photo = _make_photo()
        assert photo.status == MigrationStatus.PENDING


class TestMetadataStore:
    def test_save_and_load(self, tmp_path):
        store = MetadataStore(tmp_path)
        photo = _make_photo("abc")
        store.save(photo)

        loaded = store.load("abc")
        assert loaded is not None
        assert loaded.flickr_id == "abc"
        assert loaded.title == "Test Photo"

    def test_load_missing_returns_none(self, tmp_path):
        store = MetadataStore(tmp_path)
        assert store.load("nonexistent") is None

    def test_exists(self, tmp_path):
        store = MetadataStore(tmp_path)
        assert not store.exists("abc")
        store.save(_make_photo("abc"))
        assert store.exists("abc")

    def test_all_ids(self, tmp_path):
        store = MetadataStore(tmp_path)
        for pid in ("111", "222", "333"):
            store.save(_make_photo(pid))
        assert sorted(store.all_ids()) == ["111", "222", "333"]

    def test_all_photos(self, tmp_path):
        store = MetadataStore(tmp_path)
        for pid in ("a", "b"):
            store.save(_make_photo(pid))
        photos = store.all_photos()
        assert len(photos) == 2

    def test_by_status(self, tmp_path):
        store = MetadataStore(tmp_path)
        store.save(_make_photo("p1", status=MigrationStatus.COMPLETED))
        store.save(_make_photo("p2", status=MigrationStatus.PENDING))
        store.save(_make_photo("p3", status=MigrationStatus.COMPLETED))

        completed = store.by_status(MigrationStatus.COMPLETED)
        assert len(completed) == 2

        pending = store.by_status(MigrationStatus.PENDING)
        assert len(pending) == 1

    def test_summary(self, tmp_path):
        store = MetadataStore(tmp_path)
        store.save(_make_photo("p1", status=MigrationStatus.COMPLETED))
        store.save(_make_photo("p2", status=MigrationStatus.PENDING))
        store.save(_make_photo("p3", status=MigrationStatus.ERROR))

        summary = store.summary()
        assert summary["completed"] == 1
        assert summary["pending"] == 1
        assert summary["error"] == 1

    def test_update_existing(self, tmp_path):
        store = MetadataStore(tmp_path)
        photo = _make_photo("x")
        store.save(photo)

        photo.status = MigrationStatus.COMPLETED
        photo.google_photo_id = "gphoto_id_123"
        store.save(photo)

        loaded = store.load("x")
        assert loaded is not None
        assert loaded.status == MigrationStatus.COMPLETED
        assert loaded.google_photo_id == "gphoto_id_123"

    def test_json_file_is_readable(self, tmp_path):
        store = MetadataStore(tmp_path)
        store.save(_make_photo("readable"))
        json_file = store.photos_dir / "readable.json"
        assert json_file.exists()
        data = json.loads(json_file.read_text())
        assert data["flickr_id"] == "readable"
