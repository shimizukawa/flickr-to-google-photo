from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from click.testing import CliRunner

from flickr_to_google_photo import cli as cli_module


def test_migrate_parses_album_ids_and_skip_flags_correctly(tmp_path, monkeypatch):
    config = SimpleNamespace(data_dir=tmp_path)
    flickr = MagicMock()
    gphoto = MagicMock()
    store = MagicMock()
    migrator = MagicMock()
    migrator.fetch_all_metadata.return_value = ["111", "222"]

    monkeypatch.setattr(cli_module, "_get_config", lambda _ctx: config)
    monkeypatch.setattr(cli_module, "_make_flickr", lambda _config: flickr)
    monkeypatch.setattr(cli_module, "_make_gphoto", lambda _config: gphoto)
    monkeypatch.setattr(cli_module, "_make_store", lambda _config: store)
    monkeypatch.setattr(cli_module, "Migrator", MagicMock(return_value=migrator))
    monkeypatch.setattr(cli_module, "_print_summary", lambda _store: None)

    result = CliRunner().invoke(
        cli_module.cli,
        [
            "migrate",
            "--delete",
            "--flickr-album-id",
            "album1,album2",
            "--flickr-album-id",
            "album3",
            "--skip-fetch",
            "--skip-migrate",
        ],
    )

    assert result.exit_code == 0
    cli_module.Migrator.assert_called_once_with(
        flickr=flickr,
        gphoto=gphoto,
        store=store,
        download_dir=tmp_path / "downloads",
        delete_from_flickr=True,
        flickr_album_ids=["album1", "album2", "album3"],
        skip_fetch=True,
        skip_migrate=True,
    )
    migrator.fetch_all_metadata.assert_called_once_with()
    migrator.migrate_all.assert_called_once_with(["111", "222"])
