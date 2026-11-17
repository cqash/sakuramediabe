"""按启用顺序调用普通视频元数据插件；刮削失败不阻断文件导入。"""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from loguru import logger
from PIL import Image as PillowImage

from src.config.config import settings
from src.i18n.text import resolve_text
from src.metadata.provider import MetadataNotFoundError
from src.plugins.extensions.video_metadata import (
    VIDEO_METADATA_SOURCE_EXTENSION_KEY,
    PluginVideoMetadata,
    PluginVideoQuery,
    validate_video_metadata_extension,
)
from src.service.catalog.metadata_source_service import (
    MetadataSourceError,
    MetadataSourceFailure,
    MetadataSourceService,
)


@dataclass(frozen=True)
class VideoMetadataFetchOutcome:
    detail: PluginVideoMetadata
    source: dict[str, str | None]
    source_errors: tuple[MetadataSourceFailure, ...] = ()


class VideoMetadataSourceService(MetadataSourceService):
    sources = ()

    @classmethod
    def register(cls, registrations):
        cls.sources = tuple(
            (
                registration.plugin_id,
                registration.display_name,
                registration.display_name_i18n,
                validate_video_metadata_extension(
                    plugin_id=registration.plugin_id, extension=extension
                ),
            )
            for registration in registrations
            for extension in registration.extensions
            if extension.key == VIDEO_METADATA_SOURCE_EXTENSION_KEY
        )

    @classmethod
    def _load_plugin(
        cls,
        plugin_id: str,
        query: PluginVideoQuery,
        *,
        candidate_id: str | None = None,
    ):
        source_entry = next(
            (
                (name, name_i18n, source)
                for current_id, name, name_i18n, source in cls.enabled_plugin_sources()
                if current_id == plugin_id
            ),
            None,
        )
        if source_entry is None:
            raise MetadataSourceError(f"普通视频元数据插件未启用或未注册: {plugin_id}")
        name, name_i18n, source = source_entry
        display_name = resolve_text(name, name_i18n)
        paths: set[Path] = set()
        try:
            if candidate_id is None:
                result = source.fetch_video(query)
            else:
                if source.fetch_video_candidate is None:
                    raise MetadataSourceError(f"插件不支持候选应用: {plugin_id}")
                result = source.fetch_video_candidate(query, candidate_id)
            if result is None:
                raise MetadataNotFoundError("video", query.title)
            detail = PluginVideoMetadata.model_validate(result)
            root = (
                Path(settings.plugins.root_dir)
                / plugin_id
                / "data"
                / "metadata-tmp"
            )
            root.resolve().relative_to(
                (Path(settings.plugins.root_dir) / plugin_id).resolve()
            )
            paths.update(cls._delivery_paths(detail, root))
            for path in paths:
                with PillowImage.open(path) as image:
                    image.load()
        except MetadataNotFoundError:
            cls._cleanup_delivery(paths)
            raise
        except Exception as exc:
            cls._cleanup_delivery(paths)
            logger.warning(
                "普通视频元数据插件失败 plugin={} path={} detail={}",
                plugin_id,
                query.relative_path,
                exc,
            )
            raise MetadataSourceError(
                f"{display_name}: {type(exc).__name__}: {exc}",
                failures=(
                    MetadataSourceFailure(
                        plugin_id=plugin_id,
                        source_name=display_name,
                        detail=str(exc),
                    ),
                ),
            ) from exc
        return detail, {
            "plugin_id": plugin_id,
            "display_name": display_name,
            "source_id": detail.source_id,
            "source_url": detail.source_url,
        }, paths

    @classmethod
    @contextmanager
    def fetch(
        cls, title: str, *, relative_path: str | None = None
    ) -> Iterator[VideoMetadataFetchOutcome | None]:
        requested = (title or "").strip()
        if not requested:
            yield None
            return
        query = PluginVideoQuery(
            title=requested,
            relative_path=(relative_path or "").strip() or requested,
        )
        failures: list[MetadataSourceFailure] = []
        for plugin_id, _name, _name_i18n, _source in cls.enabled_plugin_sources():
            try:
                detail, source, paths = cls._load_plugin(plugin_id, query)
            except MetadataNotFoundError:
                continue
            except MetadataSourceError as exc:
                failures.extend(exc.failures)
                continue
            try:
                yield VideoMetadataFetchOutcome(
                    detail=detail,
                    source=source,
                    source_errors=tuple(failures),
                )
            finally:
                cls._cleanup_delivery(paths)
            return
        yield None

    @classmethod
    @contextmanager
    def fetch_candidate(
        cls, plugin_id: str, query: PluginVideoQuery, candidate_id: str
    ) -> Iterator[VideoMetadataFetchOutcome]:
        detail, source, paths = cls._load_plugin(
            plugin_id, query, candidate_id=candidate_id
        )
        try:
            yield VideoMetadataFetchOutcome(detail=detail, source=source)
        finally:
            cls._cleanup_delivery(paths)
