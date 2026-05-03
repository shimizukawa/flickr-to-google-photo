from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner

from flickr_to_google_photo import cli as cli_module


@pytest.fixture
def cli_context(tmp_path, monkeypatch):
    config = SimpleNamespace(data_dir=tmp_path)
    flickr = MagicMock()
    gphoto = MagicMock()
    store = MagicMock()
    migrator = MagicMock()

    monkeypatch.setattr(cli_module, "_get_config", lambda _ctx: config)
    monkeypatch.setattr(
        cli_module,
        "_build_migrator",
        lambda *_args, **_kwargs: (flickr, gphoto, store, migrator),
    )
    monkeypatch.setattr(cli_module, "_print_summary", MagicMock())

    return SimpleNamespace(
        config=config,
        flickr=flickr,
        gphoto=gphoto,
        store=store,
        migrator=migrator,
    )


def test_migrate_parses_new_option_names_and_dispatches_steps(monkeypatch, cli_context):
    photo_ids = ["111", "222"]
    selected_photo_ids = MagicMock(return_value=photo_ids)
    download_photos = MagicMock()
    annotate_photos = MagicMock()
    upload_photos = MagicMock()
    delete_photos = MagicMock()

    monkeypatch.setattr(cli_module, "_selected_photo_ids", selected_photo_ids)
    monkeypatch.setattr(cli_module, "_download_photos", download_photos)
    monkeypatch.setattr(cli_module, "_annotate_photos", annotate_photos)
    monkeypatch.setattr(cli_module, "_upload_photos", upload_photos)
    monkeypatch.setattr(cli_module, "_delete_photos", delete_photos)

    result = CliRunner().invoke(
        cli_module.cli,
        [
            "migrate",
            "--delete",
            "--album-id",
            "album1,album2",
            "--album-id",
            "album3",
            "--skip-fetch",
            "--skip-upload",
        ],
    )

    assert result.exit_code == 0
    cli_context.flickr.authenticate.assert_called_once_with()
    cli_context.gphoto.authenticate.assert_not_called()
    selected_photo_ids.assert_called_once_with(
        cli_context.migrator,
        photo_id=None,
        fetch_metadata_first=False,
    )
    download_photos.assert_called_once_with(cli_context.migrator, photo_ids)
    annotate_photos.assert_called_once_with(cli_context.migrator, photo_ids)
    upload_photos.assert_not_called()
    delete_photos.assert_called_once_with(cli_context.migrator, photo_ids)


def test_migrate_rejects_old_option_names():
    runner = CliRunner()

    skip_upload_result = runner.invoke(cli_module.cli, ["migrate", "--skip-migrate"])
    album_id_result = runner.invoke(
        cli_module.cli,
        ["migrate", "--flickr-album-id", "album1"],
    )

    assert skip_upload_result.exit_code != 0
    assert "No such option: --skip-migrate" in skip_upload_result.output
    assert album_id_result.exit_code != 0
    assert "No such option: --flickr-album-id" in album_id_result.output


def test_fetch_metadata_supports_photo_and_album_selection(monkeypatch, cli_context):
    fetch_single = MagicMock(return_value="123")
    cli_context.migrator.fetch_all_metadata.return_value = ["123", "456"]

    monkeypatch.setattr(cli_module, "_fetch_single_photo_metadata", fetch_single)

    single_result = CliRunner().invoke(
        cli_module.cli,
        ["fetch-metadata", "--photo-id", "123"],
    )

    assert single_result.exit_code == 0
    cli_context.flickr.authenticate.assert_called_once_with()
    fetch_single.assert_called_once_with(cli_context.migrator, "123")

    cli_context.flickr.reset_mock()
    fetch_single.reset_mock()

    album_result = CliRunner().invoke(
        cli_module.cli,
        ["fetch-metadata", "--album-id", "album1,album2"],
    )

    assert album_result.exit_code == 0
    cli_context.flickr.authenticate.assert_called_once_with()
    cli_context.migrator.fetch_all_metadata.assert_called_once_with()


@pytest.mark.parametrize(
    ("command", "args", "expect_flickr_auth", "expect_gphoto_auth", "helper_name"),
    [
        ("download", ["--photo-id", "111", "--fetch-metadata"], True, False, "_download_photos"),
        ("annotate", ["--photo-id", "111"], False, False, "_annotate_photos"),
        ("upload", ["--photo-id", "111"], False, True, "_upload_photos"),
        ("delete", ["--photo-id", "111", "--fetch-metadata"], True, False, "_delete_photos"),
    ],
)
def test_split_commands_dispatch_expected_helpers(
    monkeypatch,
    cli_context,
    command,
    args,
    expect_flickr_auth,
    expect_gphoto_auth,
    helper_name,
):
    selected_photo_ids = MagicMock(return_value=["111"])
    helper = MagicMock()

    monkeypatch.setattr(cli_module, "_selected_photo_ids", selected_photo_ids)
    monkeypatch.setattr(cli_module, helper_name, helper)

    result = CliRunner().invoke(cli_module.cli, [command, *args])

    assert result.exit_code == 0
    assert cli_context.flickr.authenticate.called is expect_flickr_auth
    assert cli_context.gphoto.authenticate.called is expect_gphoto_auth
    selected_photo_ids.assert_called_once_with(
        cli_context.migrator,
        photo_id="111",
        fetch_metadata_first="--fetch-metadata" in args,
    )
    helper.assert_called_once_with(cli_context.migrator, ["111"])
