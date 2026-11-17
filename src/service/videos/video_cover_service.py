"""非 JAV 视频封面 service。

优先保存元数据插件交付的封面；未提供时读取**第 0 帧**生成封面。
封面为增益项：读取、解码或落库失败时只记日志、返回 None，绝不阻断导入主流程。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from loguru import logger
from PIL import Image as PillowImage

av = None


def _load_av():
    """延迟导入 PyAV：仅生成封面用到，避免 API 进程启动即常驻约 18MB。"""
    global av
    if av is None:
        try:
            import av as pyav
        except ImportError:  # pragma: no cover - 由运行环境决定，测试不依赖
            return None
        av = pyav
    return av

from src.common.media_paths import media_image_root_path
from src.model import Image, VideoItem, get_database


class VideoCoverService:
    COVER_FILE_NAME = "0.webp"

    @classmethod
    def _cover_path(cls, video_item_id: int) -> Path:
        # 与缩略图同根目录，按 videos/<id>/cover 归类，便于统一签名 URL 与清理。
        return (
            media_image_root_path()
            / "videos"
            / str(video_item_id)
            / "cover"
            / cls.COVER_FILE_NAME
        )

    @classmethod
    def generate_cover(cls, video: VideoItem, video_source: Any) -> Image | None:
        """从本地路径或 seekable file-like 读取第 0 帧；失败返回 None。"""
        if _load_av() is None:
            logger.warning("Video cover skipped because pyav is unavailable video_id={}", video.id)
            return None

        if isinstance(video_source, (str, Path)):
            resolved_path = Path(video_source).expanduser().resolve()
            if not resolved_path.exists() or not resolved_path.is_file():
                logger.warning("Video cover skipped because file missing video_id={} path={}", video.id, str(resolved_path))
                return None
            av_source: Any = str(resolved_path)
            source_label = str(resolved_path)
        else:
            av_source = video_source
            source_label = getattr(video_source, "name", None) or type(video_source).__name__

        cover_path = cls._cover_path(video.id)
        container = None
        try:
            container = av.open(av_source)
            if not container.streams.video:
                logger.warning("Video cover skipped because no video stream video_id={}", video.id)
                return None
            stream = container.streams.video[0]
            # 严格取第一个可解码帧（第 0 帧），用户明确要求“首帧”。
            frame = next(container.decode(stream))
            cover_path.parent.mkdir(parents=True, exist_ok=True)
            frame.to_image().save(cover_path, format="WEBP", quality=80)
        except Exception as exc:
            logger.warning("Video cover generation failed video_id={} source={} detail={}", video.id, source_label, exc)
            return None
        finally:
            if container is not None:
                try:
                    container.close()
                except Exception:
                    pass

        return cls._persist_cover(video, cover_path, source_label=source_label)

    @classmethod
    def import_cover(cls, video: VideoItem, source_path: str | Path) -> Image | None:
        """保存元数据封面；替换已有封面时清理旧图片，失败返回 None。"""
        from uuid import uuid4

        cover_path = cls._cover_path(video.id).with_name(f"{uuid4().hex}.webp")
        obsolete_cover = video.cover_image if video.cover_image_id is not None else None
        try:
            with PillowImage.open(source_path) as source:
                cover_path.parent.mkdir(parents=True, exist_ok=True)
                source.convert("RGB").save(cover_path, format="WEBP", quality=85)
        except Exception as exc:
            logger.warning(
                "Video metadata cover skipped video_id={} path={} detail={}",
                video.id,
                source_path,
                exc,
            )
            return None
        image = cls._persist_cover(video, cover_path, source_label=str(source_path))
        if image is None:
            cover_path.unlink(missing_ok=True)
            return None
        if obsolete_cover is not None and obsolete_cover.id != image.id:
            from src.service.catalog.image_cleanup_service import ImageCleanupService

            obsolete_paths = ImageCleanupService.delete_image_record_if_unused(
                obsolete_cover
            )
            ImageCleanupService.delete_obsolete_image_files(obsolete_paths)
        return image

    @staticmethod
    def _persist_cover(
        video: VideoItem, cover_path: Path, *, source_label: str
    ) -> Image | None:
        # 封面落库是增益项，失败不能阻断已完成的媒体导入。
        try:
            image_root = media_image_root_path()
            relative_path = cover_path.relative_to(image_root).as_posix()
            with get_database().atomic():
                image = Image.create(origin=relative_path)
                video.cover_image = image
                video.save()
        except Exception as exc:
            logger.warning(
                "Video cover persist failed video_id={} path={} detail={}",
                video.id,
                source_label,
                exc,
            )
            return None
        logger.info("Video cover saved video_id={} relative_path={}", video.id, relative_path)
        return image
