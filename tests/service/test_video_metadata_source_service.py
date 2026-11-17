from datetime import date
from types import SimpleNamespace

import pytest

from src.config.config import settings
from src.plugins.contracts import PluginExtension
from src.plugins.extensions.video_metadata import (
    VIDEO_METADATA_SOURCE_EXTENSION_KEY,
    PluginVideoMetadata,
    PluginVideoMetadataSource,
    PluginVideoQuery,
    validate_video_metadata_extension,
)
from src.service.videos.video_metadata_source_service import VideoMetadataSourceService

PNG_BYTES = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000a49444154789c6300010000050001a5f645400000000049454e44ae426082"
)


def _cover_path(tmp_path, plugin_id="demo"):
    path = (
        tmp_path
        / plugin_id
        / "data"
        / "metadata-tmp"
        / "request"
        / "cover.png"
    )
    path.parent.mkdir(parents=True)
    path.write_bytes(PNG_BYTES)
    return path


def _install_sources(monkeypatch, tmp_path, sources):
    monkeypatch.setattr(settings.plugins, "root_dir", str(tmp_path))
    monkeypatch.setattr(settings.plugins, "enabled", [item[0] for item in sources])
    monkeypatch.setattr(
        VideoMetadataSourceService,
        "sources",
        tuple(
            (plugin_id, plugin_id, {}, SimpleNamespace(fetch_video=fetch))
            for plugin_id, fetch in sources
        ),
    )


def test_video_metadata_contract_accepts_optional_date_and_validates_callback():
    detail = PluginVideoMetadata(
        title="A scene", release_date="2026-02-23", cover_image_path="cover.jpg"
    )
    assert detail.release_date == date(2026, 2, 23)
    assert PluginVideoMetadata(
        title="A scene", cover_image_path="cover.jpg"
    ).release_date is None

    source = validate_video_metadata_extension(
        plugin_id="demo",
        extension=PluginExtension(
            key=VIDEO_METADATA_SOURCE_EXTENSION_KEY,
            data=PluginVideoMetadataSource(fetch_video=lambda query: None),
        ),
    )
    assert callable(source.fetch_video)
    query = PluginVideoQuery(title="Scene", relative_path="Studio/Scene.mp4")
    assert query.relative_path == "Studio/Scene.mp4"


def test_fetch_uses_first_successful_video_source_and_cleans_delivery(
    monkeypatch, tmp_path
):
    cover = _cover_path(tmp_path)
    calls = []

    def missing(query):
        calls.append(f"missing:{query.title}")

    def found(query):
        calls.append(f"found:{query.title}")
        return PluginVideoMetadata(
            title="Scraped title",
            release_date=date(2026, 2, 23),
            summary="Summary",
            cover_image_path=str(cover),
            source_id="scene-1",
            source_url="https://example.test/scene-1",
        )

    _install_sources(monkeypatch, tmp_path, [("missing", missing), ("demo", found)])

    with VideoMetadataSourceService.fetch("Original file name") as outcome:
        assert outcome is not None
        assert outcome.detail.title == "Scraped title"
        assert outcome.source["plugin_id"] == "demo"
        assert cover.is_file()

    assert calls == ["missing:Original file name", "found:Original file name"]
    assert not cover.exists()
    assert not cover.parent.exists()


def test_fetch_continues_after_invalid_image_delivery(monkeypatch, tmp_path):
    invalid = _cover_path(tmp_path, "broken")
    invalid.write_bytes(b"not an image")
    valid = _cover_path(tmp_path, "valid")

    _install_sources(
        monkeypatch,
        tmp_path,
        [
            (
                "broken",
                lambda query: PluginVideoMetadata(
                    title=query.title, cover_image_path=str(invalid)
                ),
            ),
            (
                "valid",
                lambda query: PluginVideoMetadata(
                    title="Valid", cover_image_path=str(valid)
                ),
            ),
        ],
    )

    with VideoMetadataSourceService.fetch("Title") as outcome:
        assert outcome is not None
        assert outcome.detail.title == "Valid"
        assert [item.plugin_id for item in outcome.source_errors] == ["broken"]


def test_fetch_returns_none_without_sources_or_for_blank_title(monkeypatch, tmp_path):
    _install_sources(monkeypatch, tmp_path, [])
    with VideoMetadataSourceService.fetch("Title") as outcome:
        assert outcome is None
    with VideoMetadataSourceService.fetch("  ") as outcome:
        assert outcome is None


def test_video_metadata_rejects_non_iso_date():
    with pytest.raises(ValueError):
        PluginVideoMetadata(
            title="Title", release_date="2026/02/23", cover_image_path="cover.jpg"
        )
