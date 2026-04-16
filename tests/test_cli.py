"""Tests for the CLI entry point."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from flickr_to_google_photo.cli import cli
from flickr_to_google_photo.metadata import MetadataStore, MigrationStatus, PhotoMetadata


def _make_photo(flickr_id: str = "111") -> PhotoMetadata:
    return PhotoMetadata(
        flickr_id=flickr_id,
        flickr_url=f"https://flickr.com/photos/x/{flickr_id}/",
        title="My Photo",
        description="",
        date_taken="2023-06-15 10:30:00",
        date_upload="1686820200",
        last_update="1686820200",
        tags=[],
        albums=[],
        album_ids=[],
        gps=None,
        comments=[],
        original_format="jpg",
        original_secret="secret",
        owner_nsid="nsid",
        owner_realname="Real Name",
        owner_username="user",
    )


class TestMigrateSkipFetch:
    def test_skip_fetch_does_not_call_fetch_all_metadata(self, tmp_path, monkeypatch):
        """--skip-fetch should use locally cached metadata without calling Flickr."""
        # Prepare env vars so Config() succeeds
        monkeypatch.setenv("FLICKR_API_KEY", "key")
        monkeypatch.setenv("FLICKR_API_SECRET", "secret")
        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        monkeypatch.setenv("GOOGLE_CLIENT_SECRETS_FILE", str(tmp_path / "secrets.json"))

        # Pre-populate store with one photo
        store = MetadataStore(tmp_path)
        photo = _make_photo("111")
        photo.status = MigrationStatus.COMPLETED
        store.save(photo)

        mock_migrator = MagicMock()
        mock_migrator.store = store

        with patch("flickr_to_google_photo.cli._make_flickr") as mock_make_flickr, \
             patch("flickr_to_google_photo.cli._make_gphoto") as mock_make_gphoto, \
             patch("flickr_to_google_photo.cli.Migrator", return_value=mock_migrator):

            mock_make_flickr.return_value = MagicMock()
            mock_make_gphoto.return_value = MagicMock()

            runner = CliRunner()
            result = runner.invoke(cli, ["migrate", "--skip-fetch"])

        assert result.exit_code == 0, result.output
        mock_migrator.fetch_all_metadata.assert_not_called()
        mock_migrator.migrate_all.assert_called_once()

    def test_without_skip_fetch_calls_fetch_all_metadata(self, tmp_path, monkeypatch):
        """Without --skip-fetch, fetch_all_metadata should be called."""
        monkeypatch.setenv("FLICKR_API_KEY", "key")
        monkeypatch.setenv("FLICKR_API_SECRET", "secret")
        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        monkeypatch.setenv("GOOGLE_CLIENT_SECRETS_FILE", str(tmp_path / "secrets.json"))

        store = MetadataStore(tmp_path)
        mock_migrator = MagicMock()
        mock_migrator.store = store
        mock_migrator.fetch_all_metadata.return_value = []

        with patch("flickr_to_google_photo.cli._make_flickr") as mock_make_flickr, \
             patch("flickr_to_google_photo.cli._make_gphoto") as mock_make_gphoto, \
             patch("flickr_to_google_photo.cli.Migrator", return_value=mock_migrator):

            mock_make_flickr.return_value = MagicMock()
            mock_make_gphoto.return_value = MagicMock()

            runner = CliRunner()
            result = runner.invoke(cli, ["migrate"])

        assert result.exit_code == 0, result.output
        mock_migrator.fetch_all_metadata.assert_called_once()
        mock_migrator.migrate_all.assert_called_once_with([])
