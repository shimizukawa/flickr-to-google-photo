"""
Utility functions for embedding metadata into image files before upload.

EXIF fields written (JPEG only):
- GPS (latitude, longitude, altitude)
- DateTimeOriginal
- ImageDescription / XPTitle (title)
- UserComment (description)
- XPComment (comments)
"""

from __future__ import annotations

import logging
import struct
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .metadata import PhotoMetadata

logger = logging.getLogger(__name__)


def _to_rational(value: float) -> tuple[int, int]:
    """Convert a float to a (numerator, denominator) rational pair."""
    # Use 1 000 000 as denominator for ~6 decimal places of precision
    denominator = 1_000_000
    numerator = int(round(abs(value) * denominator))
    return numerator, denominator


def _dms_rationals(degrees: float) -> list[tuple[int, int]]:
    """Convert decimal degrees to DMS rational triples for EXIF GPS."""
    d = int(degrees)
    m_float = (degrees - d) * 60
    m = int(m_float)
    s_float = (m_float - m) * 60
    s_rational = _to_rational(s_float)
    return [(d, 1), (m, 1), s_rational]


def write_exif_metadata(image_path: Path, photo: "PhotoMetadata") -> Path:
    """
    Write available metadata into the image file's EXIF (JPEG only).

    Returns the path (unchanged).  Non-JPEG files are skipped silently.
    """
    if image_path.suffix.lower() not in {".jpg", ".jpeg"}:
        logger.debug("Skipping EXIF write for non-JPEG file: %s", image_path)
        return image_path

    try:
        import piexif
    except ImportError:
        logger.warning("piexif is not installed; skipping EXIF metadata embedding.")
        return image_path

    try:
        try:
            exif_dict = piexif.load(str(image_path))
        except Exception:
            exif_dict = {"0th": {}, "Exif": {}, "GPS": {}, "1st": {}}

        zeroth = exif_dict.get("0th", {})
        exif = exif_dict.get("Exif", {})

        # Title → XPTitle (UTF-16-LE, Windows-compatible) for full Unicode support,
        # plus ImageDescription (ASCII fallback for broader tool compatibility)
        if photo.title:
            zeroth[piexif.ImageIFD.XPTitle] = photo.title.encode("utf-16-le")
            zeroth[piexif.ImageIFD.ImageDescription] = photo.title.encode("ascii", errors="replace")

        # Date taken → DateTimeOriginal
        if photo.date_taken:
            try:
                dt = datetime.fromisoformat(photo.date_taken.replace(" ", "T"))
                dt_str = dt.strftime("%Y:%m:%d %H:%M:%S")
                exif[piexif.ExifIFD.DateTimeOriginal] = dt_str.encode("ascii")
            except ValueError:
                pass

        # Description → UserComment (requires "UNICODE\0" prefix per EXIF spec)
        if photo.description:
            exif[piexif.ExifIFD.UserComment] = (
                b"UNICODE\x00" + photo.description.encode("utf-16-le")
            )

        # Comments → XPComment (UTF-16-LE, Windows-compatible)
        if photo.comments:
            comment_lines = [
                f"[{c.author_name}]: {c.content}" for c in photo.comments
            ]
            comment_text = "\n".join(comment_lines)
            zeroth[piexif.ImageIFD.XPComment] = comment_text.encode("utf-16-le")

        # GPS
        gps_ifd: dict = {}
        if photo.gps:
            lat = photo.gps.latitude
            lon = photo.gps.longitude
            gps_ifd[piexif.GPSIFD.GPSLatitudeRef] = b"N" if lat >= 0 else b"S"
            gps_ifd[piexif.GPSIFD.GPSLatitude] = _dms_rationals(abs(lat))
            gps_ifd[piexif.GPSIFD.GPSLongitudeRef] = b"E" if lon >= 0 else b"W"
            gps_ifd[piexif.GPSIFD.GPSLongitude] = _dms_rationals(abs(lon))
            if photo.gps.altitude is not None:
                alt = photo.gps.altitude
                gps_ifd[piexif.GPSIFD.GPSAltitudeRef] = b"\x00" if alt >= 0 else b"\x01"
                gps_ifd[piexif.GPSIFD.GPSAltitude] = _to_rational(abs(alt))

        exif_dict["0th"] = zeroth
        exif_dict["Exif"] = exif
        exif_dict["GPS"] = gps_ifd

        exif_bytes = piexif.dump(exif_dict)
        piexif.insert(exif_bytes, str(image_path))
        logger.debug("EXIF metadata written to %s", image_path)

    except Exception as exc:
        logger.warning("Failed to write EXIF metadata to %s: %s", image_path, exc)

    return image_path
