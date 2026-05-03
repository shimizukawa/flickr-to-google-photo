"""
CLI entry point for flickr-to-google-photo.

Usage
-----
  flickr-to-gphoto fetch-metadata   # Download all Flickr metadata to local JSON
  flickr-to-gphoto download         # Download photos from Flickr using cached metadata
  flickr-to-gphoto annotate         # Write Flickr metadata into downloaded files
  flickr-to-gphoto upload           # Upload downloaded photos to Google Photos
  flickr-to-gphoto delete           # Delete migrated photos from Flickr
  flickr-to-gphoto migrate          # Run the full migration (download → upload)
  flickr-to-gphoto migrate --delete # Also delete photos from Flickr after upload
  flickr-to-gphoto organize-local   # Organize downloaded photos into album directories
  flickr-to-gphoto status           # Show migration progress summary
  flickr-to-gphoto list-photos      # List photos and their current status
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import click

from .config import Config
from .flickr_client import FlickrClient
from .google_photo_client import GooglePhotoClient
from .local_organizer import LocalOrganizer
from .metadata import MetadataStore, MigrationStatus
from .migrator import Migrator


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        level=level,
        stream=sys.stderr,
    )


@click.group()
@click.option("--verbose", "-v", is_flag=True, default=False, help="Enable debug logging.")
@click.pass_context
def cli(ctx: click.Context, verbose: bool) -> None:
    """Migrate photos from Flickr to Google Photos."""
    _setup_logging(verbose)
    ctx.ensure_object(dict)
    ctx.obj["verbose"] = verbose


def _get_config(ctx: click.Context) -> Config:
    """Load config lazily, printing a friendly error if env vars are missing."""
    if "config" not in ctx.obj:
        try:
            ctx.obj["config"] = Config()
        except ValueError as exc:
            click.echo(f"Configuration error: {exc}", err=True)
            sys.exit(1)
    return ctx.obj["config"]


def _make_flickr(config: Config) -> FlickrClient:
    return FlickrClient(
        api_key=config.flickr_api_key,
        api_secret=config.flickr_api_secret,
        access_token=config.flickr_access_token,
        access_token_secret=config.flickr_access_token_secret,
    )


def _make_gphoto(config: Config) -> GooglePhotoClient:
    token_file = config.data_dir / "google_token.json"
    return GooglePhotoClient(
        client_secrets_file=config.google_client_secrets_file,
        token_file=token_file,
    )


def _make_store(config: Config) -> MetadataStore:
    config.ensure_data_dir()
    return MetadataStore(config.data_dir)


def _parse_comma_separated_album_ids(
    _ctx: click.Context, _param: click.Parameter, values: tuple[str, ...]
) -> list[str]:
    album_ids: list[str] = []
    for value in values:
        for album_id in value.split(","):
            album_id = album_id.strip()
            if album_id:
                album_ids.append(album_id)
    return album_ids


def _delete_option(func):
    return click.option(
        "--delete",
        "delete_from_flickr",
        is_flag=True,
        default=False,
        help="Delete photos from Flickr after successful upload to Google Photos.",
    )(func)


def _photo_id_option(func):
    return click.option(
        "--photo-id",
        default=None,
        help="Process a single photo by its Flickr ID.",
    )(func)


def _album_id_option(func):
    return click.option(
        "--album-id",
        "flickr_album_ids",
        multiple=True,
        callback=_parse_comma_separated_album_ids,
        help="Process only photos in the given Flickr album ID(s). Accepts comma-separated values.",
    )(func)


def _skip_fetch_option(func):
    return click.option(
        "--skip-fetch",
        is_flag=True,
        default=False,
        help="Skip refetching Flickr metadata and use cached local metadata instead.",
    )(func)


def _skip_upload_option(func):
    return click.option(
        "--skip-upload",
        is_flag=True,
        default=False,
        help="Skip Google Photos upload/albums after fetching metadata and saving local files.",
    )(func)


def _fetch_metadata_option(func):
    return click.option(
        "--fetch-metadata",
        "fetch_metadata_first",
        is_flag=True,
        default=False,
        help="Fetch Flickr metadata before processing when cached metadata is missing or stale.",
    )(func)


def _build_migrator(
    config: Config,
    *,
    delete_from_flickr: bool = False,
    flickr_album_ids: list[str] | None = None,
    skip_fetch: bool = False,
    skip_upload: bool = False,
) -> tuple[FlickrClient, GooglePhotoClient, MetadataStore, Migrator]:
    flickr = _make_flickr(config)
    gphoto = _make_gphoto(config)
    store = _make_store(config)
    migrator = Migrator(
        flickr=flickr,
        gphoto=gphoto,
        store=store,
        download_dir=config.data_dir / "downloads",
        delete_from_flickr=delete_from_flickr,
        flickr_album_ids=flickr_album_ids,
        skip_fetch=skip_fetch,
        skip_migrate=skip_upload,
    )
    return flickr, gphoto, store, migrator


def _fetch_single_photo_metadata(migrator: Migrator, photo_id: str) -> str:
    meta = migrator.flickr.build_photo_metadata(photo_id)
    migrator.store.save(meta)
    return photo_id


def _selected_photo_ids(
    migrator: Migrator,
    *,
    photo_id: str | None,
    fetch_metadata_first: bool,
) -> list[str]:
    if photo_id:
        if fetch_metadata_first:
            return [_fetch_single_photo_metadata(migrator, photo_id)]

        if migrator.store.exists(photo_id):
            return [photo_id]

        raise click.ClickException(
            f"No cached metadata found for {photo_id}. "
            "Run the command again with --fetch-metadata or use fetch-metadata first."
        )

    if fetch_metadata_first:
        return migrator.fetch_all_metadata()

    return migrator._cached_photo_ids()


def _load_photo_or_raise(store: MetadataStore, photo_id: str):
    photo = store.load(photo_id)
    if photo is None:
        raise click.ClickException(f"No cached metadata found for {photo_id}.")
    return photo


def _download_photos(migrator: Migrator, photo_ids: list[str]) -> None:
    for photo_id in photo_ids:
        photo = _load_photo_or_raise(migrator.store, photo_id)
        migrator._download(photo)


def _annotate_photos(migrator: Migrator, photo_ids: list[str]) -> None:
    for photo_id in photo_ids:
        photo = _load_photo_or_raise(migrator.store, photo_id)
        if not photo.local_path or not Path(photo.local_path).exists():
            raise click.ClickException(
                f"Photo {photo_id} is not downloaded yet. Run `download` first."
            )
        migrator._write_exif(Path(photo.local_path), photo)


def _upload_photos(migrator: Migrator, photo_ids: list[str]) -> None:
    for photo_id in photo_ids:
        photo = _load_photo_or_raise(migrator.store, photo_id)
        if not photo.local_path or not Path(photo.local_path).exists():
            raise click.ClickException(
                f"Photo {photo_id} is not downloaded yet. Run `download` first."
            )
        media_item = migrator._upload(Path(photo.local_path), photo)
        migrator._add_to_albums(media_item["id"], photo)


def _delete_photos(migrator: Migrator, photo_ids: list[str]) -> None:
    for photo_id in photo_ids:
        photo = _load_photo_or_raise(migrator.store, photo_id)
        migrator._delete_from_flickr(photo)


# ------------------------------------------------------------------
# Commands
# ------------------------------------------------------------------

@cli.command("fetch-metadata")
@_album_id_option
@_photo_id_option
@click.pass_context
def fetch_metadata(ctx: click.Context, photo_id: str | None, flickr_album_ids: list[str]) -> None:
    """Fetch Flickr photo metadata and store it locally."""
    config: Config = _get_config(ctx)
    flickr, _gphoto, store, migrator = _build_migrator(
        config,
        flickr_album_ids=flickr_album_ids,
    )
    flickr.authenticate()

    if photo_id:
        _fetch_single_photo_metadata(migrator, photo_id)
        click.echo(f"Fetched metadata for photo: {photo_id}")
        return

    click.echo("Fetching photo metadata from Flickr…")
    photo_ids = migrator.fetch_all_metadata()
    click.echo(f"Fetched metadata for {len(photo_ids)} photos.")
    _print_summary(store)


@cli.command("migrate")
@_delete_option
@_photo_id_option
@_album_id_option
@_skip_fetch_option
@_skip_upload_option
@click.pass_context
def migrate(
    ctx: click.Context,
    delete_from_flickr: bool,
    photo_id: str | None,
    flickr_album_ids: list[str],
    skip_fetch: bool,
    skip_upload: bool,
) -> None:
    """Migrate photos from Flickr to Google Photos."""
    config: Config = _get_config(ctx)
    flickr, gphoto, store, migrator = _build_migrator(
        config,
        delete_from_flickr=delete_from_flickr,
        flickr_album_ids=flickr_album_ids,
        skip_fetch=skip_fetch,
        skip_upload=skip_upload,
    )
    flickr.authenticate()

    photo_ids = _selected_photo_ids(
        migrator,
        photo_id=photo_id,
        fetch_metadata_first=not skip_fetch,
    )
    _download_photos(migrator, photo_ids)
    _annotate_photos(migrator, photo_ids)

    if not skip_upload:
        gphoto.authenticate()
        _upload_photos(migrator, photo_ids)

    if delete_from_flickr:
        _delete_photos(migrator, photo_ids)

    click.echo("Migration complete.")
    _print_summary(store)


@cli.command("download")
@_fetch_metadata_option
@_album_id_option
@_photo_id_option
@click.pass_context
def download(
    ctx: click.Context,
    fetch_metadata_first: bool,
    photo_id: str | None,
    flickr_album_ids: list[str],
) -> None:
    """Download photos from Flickr."""
    config: Config = _get_config(ctx)
    flickr, _gphoto, store, migrator = _build_migrator(
        config,
        flickr_album_ids=flickr_album_ids,
    )
    flickr.authenticate()

    photo_ids = _selected_photo_ids(
        migrator,
        photo_id=photo_id,
        fetch_metadata_first=fetch_metadata_first,
    )
    _download_photos(migrator, photo_ids)
    click.echo(f"Downloaded {len(photo_ids)} photos.")
    _print_summary(store)


@cli.command("annotate")
@_fetch_metadata_option
@_album_id_option
@_photo_id_option
@click.pass_context
def annotate(
    ctx: click.Context,
    fetch_metadata_first: bool,
    photo_id: str | None,
    flickr_album_ids: list[str],
) -> None:
    """Write metadata into downloaded photo files."""
    config: Config = _get_config(ctx)
    flickr, _gphoto, store, migrator = _build_migrator(
        config,
        flickr_album_ids=flickr_album_ids,
    )
    if fetch_metadata_first:
        flickr.authenticate()

    photo_ids = _selected_photo_ids(
        migrator,
        photo_id=photo_id,
        fetch_metadata_first=fetch_metadata_first,
    )
    _annotate_photos(migrator, photo_ids)
    click.echo(f"Annotated {len(photo_ids)} photos.")
    _print_summary(store)


@cli.command("upload")
@_fetch_metadata_option
@_album_id_option
@_photo_id_option
@click.pass_context
def upload(
    ctx: click.Context,
    fetch_metadata_first: bool,
    photo_id: str | None,
    flickr_album_ids: list[str],
) -> None:
    """Upload downloaded photos to Google Photos."""
    config: Config = _get_config(ctx)
    flickr, gphoto, store, migrator = _build_migrator(
        config,
        flickr_album_ids=flickr_album_ids,
    )
    if fetch_metadata_first:
        flickr.authenticate()
    gphoto.authenticate()

    photo_ids = _selected_photo_ids(
        migrator,
        photo_id=photo_id,
        fetch_metadata_first=fetch_metadata_first,
    )
    _upload_photos(migrator, photo_ids)
    click.echo(f"Uploaded {len(photo_ids)} photos.")
    _print_summary(store)


@cli.command("delete")
@_fetch_metadata_option
@_album_id_option
@_photo_id_option
@click.pass_context
def delete(
    ctx: click.Context,
    fetch_metadata_first: bool,
    photo_id: str | None,
    flickr_album_ids: list[str],
) -> None:
    """Delete photos from Flickr."""
    config: Config = _get_config(ctx)
    flickr, _gphoto, store, migrator = _build_migrator(
        config,
        delete_from_flickr=True,
        flickr_album_ids=flickr_album_ids,
    )
    flickr.authenticate()

    photo_ids = _selected_photo_ids(
        migrator,
        photo_id=photo_id,
        fetch_metadata_first=fetch_metadata_first,
    )
    _delete_photos(migrator, photo_ids)
    click.echo(f"Deleted {len(photo_ids)} photos from Flickr.")
    _print_summary(store)


@cli.command("organize-local")
@click.option(
    "--dest",
    "dest_dir",
    default=None,
    type=click.Path(file_okay=False, path_type=Path),
    help="Destination directory for organized photos. Defaults to <DATA_DIR>/organized.",
)
@click.option(
    "--copy",
    "copy",
    is_flag=True,
    default=False,
    help="Copy files instead of moving them (originals are preserved).",
)
@click.option(
    "--photo-id",
    default=None,
    help="Organize a single photo by its Flickr ID instead of all photos.",
)
@click.pass_context
def organize_local(ctx: click.Context, dest_dir: Path | None, copy: bool, photo_id: str | None) -> None:
    """Organize downloaded photos into album-based local directories.

    Each photo is placed under <dest>/<album_name>/.  Photos that belong to
    multiple albums are copied into each album directory.  Photos with no
    album go into an 'uncategorized' subdirectory.

    Flickr comments are embedded into the photo's EXIF data (XPComment field)
    in addition to the standard EXIF metadata.
    """
    config: Config = _get_config(ctx)
    store = _make_store(config)

    if dest_dir is None:
        dest_dir = config.data_dir / "organized"

    organizer = LocalOrganizer(store=store, dest_dir=dest_dir, copy=copy)

    if photo_id:
        click.echo(f"Organizing single photo: {photo_id}")
        organizer.organize_one_by_id(photo_id)
    else:
        all_ids = store.all_ids()
        click.echo(f"Organizing {len(all_ids)} photos into {dest_dir}…")
        organizer.organize_all(all_ids)

    click.echo("Done.")


@cli.command("status")
@click.pass_context
def status(ctx: click.Context) -> None:
    """Show a summary of migration progress."""
    config: Config = _get_config(ctx)
    store = _make_store(config)
    _print_summary(store)


@cli.command("list-photos")
@click.option(
    "--filter-status",
    default=None,
    type=click.Choice([s.value for s in MigrationStatus], case_sensitive=False),
    help="Filter by migration status.",
)
@click.pass_context
def list_photos(ctx: click.Context, filter_status: str | None) -> None:
    """List photos and their current migration status."""
    config: Config = _get_config(ctx)
    store = _make_store(config)

    photos = (
        store.by_status(MigrationStatus(filter_status))
        if filter_status
        else store.all_photos()
    )

    if not photos:
        click.echo("No photos found.")
        return

    for photo in photos:
        google_url = photo.google_photo_url or "(not uploaded)"
        click.echo(
            f"{photo.flickr_id:>12}  [{photo.status.value:^30}]  "
            f"'{photo.title[:40]}'  → {google_url}"
        )


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _print_summary(store: MetadataStore) -> None:
    summary = store.summary()
    total = sum(summary.values())
    click.echo(f"\nMigration summary ({total} total photos):")
    for status_val, count in sorted(summary.items()):
        click.echo(f"  {status_val:35} : {count}")
