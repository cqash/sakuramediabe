"""为 Agent 和人工审查提供短期普通视频候选，并按候选应用元数据。"""

from __future__ import annotations

import time
from dataclasses import dataclass
from threading import RLock
from uuid import uuid4

from src.api.exception.errors import ApiError
from src.i18n.text import resolve_text
from src.metadata.provider import MetadataNotFoundError
from src.model import Media, VideoItem
from src.plugins.extensions.video_metadata import (
    PluginVideoCandidate,
    PluginVideoQuery,
)
from src.schema.videos.metadata import (
    VideoMetadataCandidateResource,
    VideoMetadataCandidateSearchRequest,
    VideoMetadataCandidateSearchResponse,
    VideoMetadataSourceErrorResource,
)
from src.service.catalog.metadata_source_service import MetadataSourceError
from src.service.videos.video_item_service import VideoItemService
from src.service.videos.video_metadata_source_service import VideoMetadataSourceService


@dataclass(frozen=True)
class _CandidateEntry:
    query: PluginVideoQuery
    plugin_id: str
    candidate: PluginVideoCandidate
    video_id: int | None
    expires_at: float


class VideoMetadataSearchService:
    CACHE_TTL_SECONDS = 30 * 60
    MAX_CANDIDATES_PER_SOURCE = 20
    _entries: dict[str, _CandidateEntry] = {}
    _lock = RLock()

    @classmethod
    def _query_for_video(cls, video_id: int) -> PluginVideoQuery:
        video = VideoItem.get_or_none(VideoItem.id == video_id)
        if video is None:
            raise ApiError(404, "video_item_not_found", "Video item not found")
        extra = video.extra if isinstance(video.extra, dict) else {}
        imported = extra.get("import_query")
        imported = imported if isinstance(imported, dict) else {}
        title = str(imported.get("title") or video.title).strip()
        relative_path = str(imported.get("relative_path") or "").strip()
        if not relative_path:
            media = Media.select(Media.file_name).where(Media.video_item == video).first()
            relative_path = media.file_name if media is not None else f"{title}.mp4"
        if not title:
            raise ApiError(422, "invalid_video_metadata_query", "视频缺少可检索标题")
        return PluginVideoQuery(title=title, relative_path=relative_path)

    @classmethod
    def _query_for_request(
        cls, payload: VideoMetadataCandidateSearchRequest
    ) -> tuple[PluginVideoQuery, int | None]:
        if payload.video_id is not None:
            return cls._query_for_video(payload.video_id), payload.video_id
        title = (payload.title or "").strip()
        return (
            PluginVideoQuery(
                title=title,
                relative_path=(payload.relative_path or "").strip() or title,
            ),
            None,
        )

    @classmethod
    def _purge_expired(cls) -> None:
        now = time.monotonic()
        with cls._lock:
            expired = [key for key, entry in cls._entries.items() if entry.expires_at <= now]
            for key in expired:
                cls._entries.pop(key, None)

    @classmethod
    def search(
        cls, payload: VideoMetadataCandidateSearchRequest
    ) -> VideoMetadataCandidateSearchResponse:
        query, video_id = cls._query_for_request(payload)
        cls._purge_expired()
        candidates: list[VideoMetadataCandidateResource] = []
        source_errors: list[VideoMetadataSourceErrorResource] = []
        for plugin_id, name, name_i18n, source in VideoMetadataSourceService.enabled_plugin_sources():
            if source.search_videos is None:
                continue
            display_name = resolve_text(name, name_i18n)
            try:
                results = source.search_videos(query) or []
                for raw in results[: cls.MAX_CANDIDATES_PER_SOURCE]:
                    candidate = PluginVideoCandidate.model_validate(raw)
                    candidate_id = uuid4().hex
                    entry = _CandidateEntry(
                        query=query,
                        plugin_id=plugin_id,
                        candidate=candidate,
                        video_id=video_id,
                        expires_at=time.monotonic() + cls.CACHE_TTL_SECONDS,
                    )
                    with cls._lock:
                        cls._entries[candidate_id] = entry
                    candidates.append(
                        VideoMetadataCandidateResource(
                            candidate_id=candidate_id,
                            plugin_id=plugin_id,
                            source_name=display_name,
                            source_id=candidate.source_id,
                            title=candidate.title,
                            release_date=(
                                candidate.release_date.isoformat()
                                if candidate.release_date is not None
                                else None
                            ),
                            summary=candidate.summary,
                            site_name=candidate.site_name,
                            performers=candidate.performers,
                            score=candidate.score,
                            source_url=candidate.source_url,
                        )
                    )
            except Exception as exc:
                source_errors.append(
                    VideoMetadataSourceErrorResource(
                        source=plugin_id,
                        source_name=display_name,
                        reason="metadata_fetch_failed",
                        detail=str(exc),
                    )
                )
        return VideoMetadataCandidateSearchResponse(
            title=query.title,
            relative_path=query.relative_path,
            candidates=candidates,
            source_errors=source_errors,
        )

    @classmethod
    def _entry(cls, candidate_id: str, video_id: int) -> _CandidateEntry:
        cls._purge_expired()
        with cls._lock:
            entry = cls._entries.get(candidate_id.strip())
        if entry is None or (
            entry.video_id is not None and entry.video_id != video_id
        ):
            raise ApiError(422, "invalid_video_metadata_candidate", "视频候选无效、已失效或不属于该视频")
        return entry

    @classmethod
    def apply(cls, video_id: int, candidate_id: str):
        entry = cls._entry(candidate_id, video_id)
        try:
            with VideoMetadataSourceService.fetch_candidate(
                entry.plugin_id, entry.query, entry.candidate.candidate_id
            ) as outcome:
                detail = outcome.detail
                if (
                    entry.candidate.source_id
                    and detail.source_id
                    and entry.candidate.source_id != detail.source_id
                ):
                    raise ApiError(
                        422,
                        "video_metadata_candidate_mismatch",
                        "插件返回的内容与候选不一致",
                    )
                return VideoItemService.apply_metadata(
                    video_id, detail, outcome.source
                )
        except MetadataNotFoundError as exc:
            raise ApiError(422, "video_metadata_candidate_unavailable", "候选已无法从来源获取") from exc
        except MetadataSourceError as exc:
            raise ApiError(502, "video_metadata_fetch_failed", str(exc)) from exc

    @classmethod
    def clear_cache(cls) -> None:
        with cls._lock:
            cls._entries.clear()
