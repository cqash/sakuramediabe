"""按文件信息交付普通视频元数据；图片由插件下载，宿主接管后入库。"""

from collections.abc import Callable
from datetime import date

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.plugins.contracts import PluginExtension

VIDEO_METADATA_SOURCE_EXTENSION_KEY = "catalog.video_metadata_source"
VIDEO_METADATA_SOURCE_HOST_API_VERSION = 19


class PluginVideoQuery(BaseModel):
    model_config = ConfigDict(
        extra="forbid", str_strip_whitespace=True, revalidate_instances="always"
    )

    title: str = Field(min_length=1, max_length=1024)
    relative_path: str = Field(min_length=1, max_length=4096)


def _validate_optional_date(value):
    if value is None:
        return None
    if isinstance(value, str):
        parsed = date.fromisoformat(value)
        if parsed.isoformat() == value:
            return parsed
    elif type(value) is date:
        return value
    raise ValueError("release_date 必须是 YYYY-MM-DD 日期或 None")


class PluginVideoMetadata(BaseModel):
    model_config = ConfigDict(
        extra="forbid", str_strip_whitespace=True, revalidate_instances="always"
    )

    title: str = Field(min_length=1, max_length=255)
    release_date: date | None = None
    cover_image_path: str = Field(min_length=1)
    summary: str = ""
    source_url: str | None = None
    source_id: str | None = None

    @field_validator("release_date", mode="before")
    @classmethod
    def validate_date(cls, value):
        return _validate_optional_date(value)

    @field_validator("summary", mode="before")
    @classmethod
    def empty_summary(cls, value):
        return "" if value is None else value


class PluginVideoCandidate(BaseModel):
    model_config = ConfigDict(
        extra="forbid", str_strip_whitespace=True, revalidate_instances="always"
    )

    candidate_id: str = Field(min_length=1, max_length=512)
    title: str = Field(min_length=1, max_length=255)
    release_date: date | None = None
    summary: str = ""
    site_name: str | None = None
    performers: list[str] = Field(default_factory=list)
    score: float = Field(default=0.0, ge=0.0, le=1.0)
    source_url: str | None = None
    source_id: str | None = None

    @field_validator("release_date", mode="before")
    @classmethod
    def validate_date(cls, value):
        return _validate_optional_date(value)

    @field_validator("summary", mode="before")
    @classmethod
    def empty_summary(cls, value):
        return "" if value is None else value

    @field_validator("performers", mode="before")
    @classmethod
    def empty_performers(cls, value):
        return [] if value is None else value


class PluginVideoMetadataSource(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")

    fetch_video: Callable[[PluginVideoQuery], PluginVideoMetadata | None]
    search_videos: (
        Callable[[PluginVideoQuery], list[PluginVideoCandidate]] | None
    ) = None
    fetch_video_candidate: (
        Callable[[PluginVideoQuery, str], PluginVideoMetadata | None] | None
    ) = None

    @model_validator(mode="after")
    def validate_candidate_callbacks(self):
        if (self.search_videos is None) != (self.fetch_video_candidate is None):
            raise ValueError("search_videos 与 fetch_video_candidate 必须同时提供")
        return self


def validate_video_metadata_extension(
    *, plugin_id: str, extension: PluginExtension
) -> PluginVideoMetadataSource:
    return PluginVideoMetadataSource.model_validate(extension.data)
