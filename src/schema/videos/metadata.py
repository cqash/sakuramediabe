from pydantic import Field, model_validator

from src.schema.common.base import SchemaModel


class VideoMetadataCandidateSearchRequest(SchemaModel):
    video_id: int | None = Field(default=None, gt=0)
    title: str | None = Field(default=None, min_length=1, max_length=1024)
    relative_path: str | None = Field(default=None, min_length=1, max_length=4096)

    @model_validator(mode="after")
    def validate_query(self):
        self.title = self.title.strip() if self.title is not None else None
        self.relative_path = (
            self.relative_path.strip() if self.relative_path is not None else None
        )
        if self.title == "" or self.relative_path == "":
            raise ValueError("title 和 relative_path 不能为空白")
        if self.video_id is None and self.title is None:
            raise ValueError("video_id 或 title 必须提供一个")
        if self.video_id is not None and (self.title is not None or self.relative_path is not None):
            raise ValueError("使用 video_id 时不能再传 title 或 relative_path")
        return self


class VideoMetadataCandidateResource(SchemaModel):
    candidate_id: str
    plugin_id: str
    source_name: str
    source_id: str | None = None
    title: str
    release_date: str | None = None
    summary: str = ""
    site_name: str | None = None
    performers: list[str] = Field(default_factory=list)
    score: float = 0.0
    source_url: str | None = None


class VideoMetadataSourceErrorResource(SchemaModel):
    source: str
    source_name: str
    reason: str
    detail: str


class VideoMetadataCandidateSearchResponse(SchemaModel):
    title: str
    relative_path: str
    candidates: list[VideoMetadataCandidateResource] = Field(default_factory=list)
    source_errors: list[VideoMetadataSourceErrorResource] = Field(default_factory=list)


class VideoMetadataApplyRequest(SchemaModel):
    candidate_id: str = Field(min_length=1, max_length=1024)
