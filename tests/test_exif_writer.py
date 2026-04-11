"""Tests for the EXIF writer utility."""

from __future__ import annotations

import io
import struct
from pathlib import Path

import pytest

from flickr_to_google_photo.exif_writer import (
    _dms_rationals,
    _to_rational,
    write_exif_metadata,
)
from flickr_to_google_photo.metadata import GpsInfo, PhotoMetadata


def _make_photo(**kwargs) -> PhotoMetadata:
    defaults = dict(
        flickr_id="1",
        flickr_url="https://flickr.com/photos/x/1/",
        title="Title",
        description="Description",
        date_taken="2023-06-15 10:30:00",
        date_upload="1686820200",
        last_update="1686820200",
        tags=[],
        albums=[],
        album_ids=[],
        gps=GpsInfo(latitude=35.6812, longitude=139.7671, altitude=100.0),
        comments=[],
        original_format="jpg",
        original_secret="",
        owner_nsid="",
        owner_realname="",
        owner_username="",
    )
    defaults.update(kwargs)
    return PhotoMetadata(**defaults)


class TestToRational:
    def test_positive(self):
        n, d = _to_rational(1.5)
        assert n / d == pytest.approx(1.5, rel=1e-5)

    def test_zero(self):
        n, d = _to_rational(0.0)
        assert n == 0

    def test_small(self):
        n, d = _to_rational(0.000001)
        assert n / d == pytest.approx(0.000001, rel=1e-3)


class TestDmsRationals:
    def test_whole_degrees(self):
        dms = _dms_rationals(35.0)
        assert dms[0] == (35, 1)  # degrees
        assert dms[1] == (0, 1)   # minutes
        # seconds ≈ 0
        n, d = dms[2]
        assert n / d == pytest.approx(0.0, abs=1e-4)

    def test_decimal_degrees(self):
        dms = _dms_rationals(35.6812)
        deg, mins, secs = dms
        assert deg == (35, 1)
        # minutes should be approximately 40
        assert mins[0] / mins[1] == pytest.approx(40, abs=1)
        # reconstructed value should match original
        d = deg[0] / deg[1]
        m = mins[0] / mins[1]
        s = secs[0] / secs[1]
        reconstructed = d + m / 60 + s / 3600
        assert reconstructed == pytest.approx(35.6812, rel=1e-4)


class TestWriteExifMetadata:
    def test_non_jpeg_skipped(self, tmp_path):
        png = tmp_path / "image.png"
        png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
        photo = _make_photo()
        result = write_exif_metadata(png, photo)
        assert result == png

    def test_returns_path(self, tmp_path):
        # Without a real JPEG file, piexif will raise an exception which is caught
        fake_jpg = tmp_path / "image.jpg"
        fake_jpg.write_bytes(b"\xff\xd8\xff" + b"\x00" * 50)  # minimal fake JPEG header
        photo = _make_photo()
        result = write_exif_metadata(fake_jpg, photo)
        assert result == fake_jpg

    def test_no_gps_no_crash(self, tmp_path):
        fake_jpg = tmp_path / "image.jpg"
        fake_jpg.write_bytes(b"\xff\xd8\xff" + b"\x00" * 50)
        photo = _make_photo(gps=None)
        result = write_exif_metadata(fake_jpg, photo)
        assert result == fake_jpg
