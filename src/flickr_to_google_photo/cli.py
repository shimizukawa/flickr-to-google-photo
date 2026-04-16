"""
CLI entry point for flickr-to-google-photo.

Usage
-----
  flickr-to-gphoto fetch-metadata   # Download all Flickr metadata to local JSON
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


# ------------------------------------------------------------------
# Commands
# ------------------------------------------------------------------

@cli.command("fetch-metadata")
@click.pass_context
def fetch_metadata(ctx: click.Context) -> None:
    """Fetch all photo metadata from Flickr and store locally."""
    config: Config = _get_config(ctx)
    flickr = _make_flickr(config)
    flickr.authenticate()
    store = _make_store(config)

    click.echo("Fetching photo list from Flickr…")
    photo_ids = flickr.get_all_photo_ids()
    click.echo(f"Found {len(photo_ids)} photos. Fetching metadata…")

    for idx, pid in enumerate(photo_ids, start=1):
        if store.exists(pid):
            click.echo(f"[{idx}/{len(photo_ids)}] {pid} – already cached, skipping.")
            continue
        try:
            meta = flickr.build_photo_metadata(pid)
            store.save(meta)
            click.echo(f"[{idx}/{len(photo_ids)}] {pid} – '{meta.title}' saved.")
        except Exception as exc:
            click.echo(f"[{idx}/{len(photo_ids)}] {pid} – ERROR: {exc}", err=True)

    click.echo("Done.")


@cli.command("migrate")
@click.option(
    "--delete",
    "delete_from_flickr",
    is_flag=True,
    default=False,
    help="Delete photos from Flickr after successful upload to Google Photos.",
)
@click.option(
    "--photo-id",
    default=None,
    help="Migrate a single photo by its Flickr ID instead of all photos.",
)
@click.option(
    "--skip-fetch",
    "skip_fetch",
    is_flag=True,
    default=False,
    help="Skip fetching metadata from Flickr and use only locally cached metadata. Useful for debugging.",
)
@click.pass_context
def migrate(ctx: click.Context, delete_from_flickr: bool, photo_id: str | None, skip_fetch: bool) -> None:
    """Migrate photos from Flickr to Google Photos."""
    config: Config = _get_config(ctx)

    flickr = _make_flickr(config)
    flickr.authenticate()

    gphoto = _make_gphoto(config)
    gphoto.authenticate()

    store = _make_store(config)
    download_dir = config.data_dir / "downloads"

    migrator = Migrator(
        flickr=flickr,
        gphoto=gphoto,
        store=store,
        download_dir=download_dir,
        delete_from_flickr=delete_from_flickr,
    )

    if photo_id:
        click.echo(f"Migrating single photo: {photo_id}")
        migrator.migrate_one_by_id(photo_id)
    elif skip_fetch:
        all_ids = store.all_ids()
        click.echo(f"Skipping Flickr metadata fetch. Using {len(all_ids)} locally cached photos…")
        migrator.migrate_all(all_ids)
    else:
        click.echo("Fetching metadata for all Flickr photos…")
        all_ids = migrator.fetch_all_metadata()
        click.echo(f"Starting migration of {len(all_ids)} photos…")
        migrator.migrate_all(all_ids)

    click.echo("Migration complete.")
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
