from datetime import date
from types import SimpleNamespace

from src.common.media_paths import media_image_root_path
from src.config.config import settings
from src.model import Media, MediaLibrary, VideoItem
from src.plugins.extensions.video_metadata import PluginVideoMetadata
from src.plugins.provider_protocol import ImportFile, StagedMedia
from src.service.transfers.imports.import_service import MediaImportService
from src.service.videos.video_metadata_source_service import VideoMetadataSourceService

PNG_BYTES = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000a49444154789c6300010000050001a5f645400000000049454e44ae426082"
)


class _Storage:
    def scan_import_source(self, *, source_ref):
        return (
            ImportFile(
                source_ref={"source": "origin"},
                name="Site.26.02.23.Performer.Name.mp4",
                relative_path="Site.26.02.23.Performer.Name.mp4",
                size_bytes=100,
                is_video=True,
            ),
        )

    def stage_import_file(self, **_kwargs):
        return StagedMedia(
            storage_ref={"target": "video.mp4"},
            receipt={"receipt": "video"},
            size_bytes=100,
            duration_seconds=2511,
            video_info=None,
        )

    def finalize_import(self, *, receipt):
        assert receipt == {"receipt": "video"}

    def compute_file_hash(self, *, media):
        return "f" * 64


def _library():
    return MediaLibrary.create(
        name="video-metadata-library", provider_key="test", provider_config={}
    )


def test_video_import_applies_plugin_metadata_and_cover(test_db, monkeypatch, tmp_path):
    library = _library()
    plugin_root = tmp_path / "plugins"
    delivered_cover = (
        plugin_root
        / "demo"
        / "data"
        / "metadata-tmp"
        / "request"
        / "cover.png"
    )
    delivered_cover.parent.mkdir(parents=True)
    delivered_cover.write_bytes(PNG_BYTES)
    monkeypatch.setattr(settings.plugins, "root_dir", str(plugin_root))
    monkeypatch.setattr(settings.plugins, "enabled", ["demo"])
    monkeypatch.setattr(
        settings.media, "import_image_root_path", str(tmp_path / "images")
    )
    queries = []

    def fetch_video(query):
        queries.append(query)
        return PluginVideoMetadata(
            title="Scraped scene title",
            release_date=date(2026, 2, 23),
            summary="Scraped summary",
            cover_image_path=str(delivered_cover),
            source_id="scene-1",
            source_url="https://example.test/scene-1",
        )

    monkeypatch.setattr(
        VideoMetadataSourceService,
        "sources",
        (("demo", "Demo", {}, SimpleNamespace(fetch_video=fetch_video)),),
    )
    monkeypatch.setattr(
        MediaImportService,
        "_generate_video_cover",
        lambda _self, **_kwargs: (_ for _ in ()).throw(
            AssertionError("scraped cover must not be replaced by a video frame")
        ),
    )

    result = MediaImportService(
        provider=_Storage(), catalog_import_service=object()
    ).import_from_source({"source": "directory"}, library.id, media_kind="video")

    assert result.imported_count == 1, result.failed_files
    assert result.failed_count == 0
    assert [query.title for query in queries] == ["Site.26.02.23.Performer.Name"]
    assert [query.relative_path for query in queries] == [
        "Site.26.02.23.Performer.Name.mp4"
    ]
    video = VideoItem.get()
    assert video.title == "Scraped scene title"
    assert video.summary == "Scraped summary"
    assert video.release_date.date() == date(2026, 2, 23)
    assert video.extra["metadata_source"]["plugin_id"] == "demo"
    assert video.extra["metadata_source"]["source_id"] == "scene-1"
    assert Media.get().video_item_id == video.id
    assert video.cover_image_id is not None
    stored_cover = media_image_root_path() / video.cover_image.origin
    assert stored_cover.is_file()
    assert stored_cover.suffix == ".webp"
    assert not delivered_cover.exists()


def test_video_import_without_metadata_match_keeps_file_name(
    test_db, monkeypatch, tmp_path
):
    library = _library()
    monkeypatch.setattr(VideoMetadataSourceService, "sources", ())
    monkeypatch.setattr(
        settings.media, "import_image_root_path", str(tmp_path / "images")
    )
    generated = []
    monkeypatch.setattr(
        MediaImportService,
        "_generate_video_cover",
        lambda _self, **kwargs: generated.append(kwargs["video"].id),
    )

    result = MediaImportService(
        provider=_Storage(), catalog_import_service=object()
    ).import_from_source({"source": "directory"}, library.id, media_kind="video")

    assert result.imported_count == 1, result.failed_files
    assert result.failed_count == 0
    video = VideoItem.get()
    assert video.title == "Site.26.02.23.Performer.Name"
    assert video.summary == ""
    assert video.release_date is None
    assert video.extra["import_query"] == {
        "title": "Site.26.02.23.Performer.Name",
        "relative_path": "Site.26.02.23.Performer.Name.mp4",
    }
    assert "metadata_source" not in video.extra
    assert generated == [video.id]
