from datetime import date
from types import SimpleNamespace

import pytest

from src.config.config import settings
from src.model import Image, VideoItem
from src.plugins.extensions.video_metadata import (
    PluginVideoCandidate,
    PluginVideoMetadata,
)
from src.service.videos.video_metadata_search_service import VideoMetadataSearchService
from src.service.videos.video_metadata_source_service import VideoMetadataSourceService

PNG_BYTES = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000a49444154789c6300010000050001a5f645400000000049454e44ae426082"
)


def _auth_headers(client, account_user):
    response = client.post(
        "/auth/tokens",
        json={"username": account_user.username, "password": "password123"},
    )
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.fixture(autouse=True)
def _clear_candidate_cache():
    VideoMetadataSearchService.clear_cache()
    yield
    VideoMetadataSearchService.clear_cache()


def test_unmatched_videos_require_auth_and_exclude_scraped_items(client, account_user):
    unmatched = VideoItem.create(
        title="Unmatched",
        extra={"import_query": {"title": "Unmatched", "relative_path": "Unmatched.mp4"}},
    )
    VideoItem.create(
        title="Matched",
        extra={"metadata_source": {"plugin_id": "demo"}},
    )
    headers = _auth_headers(client, account_user)

    assert client.get("/videos/unmatched").status_code == 401
    response = client.get("/videos/unmatched", headers=headers)

    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert response.json()["items"][0]["id"] == unmatched.id


def test_agent_can_search_bound_candidates_and_apply_metadata(
    client, account_user, monkeypatch, tmp_path
):
    image_root = tmp_path / "images"
    plugin_root = tmp_path / "plugins"
    delivered_cover = (
        plugin_root / "demo" / "data" / "metadata-tmp" / "request" / "cover.png"
    )
    delivered_cover.parent.mkdir(parents=True)
    delivered_cover.write_bytes(PNG_BYTES)
    old_cover_path = image_root / "videos" / "old.webp"
    old_cover_path.parent.mkdir(parents=True)
    old_cover_path.write_bytes(PNG_BYTES)
    old_cover = Image.create(origin="videos/old.webp")
    video = VideoItem.create(
        title="Original file",
        cover_image=old_cover,
        extra={
            "import_query": {
                "title": "Original file",
                "relative_path": "Studio/Original file.mp4",
            }
        },
    )
    monkeypatch.setattr(settings.media, "import_image_root_path", str(image_root))
    monkeypatch.setattr(settings.plugins, "root_dir", str(plugin_root))
    monkeypatch.setattr(settings.plugins, "enabled", ["demo"])
    queries = []

    def search_videos(query):
        queries.append(query)
        return [
            PluginVideoCandidate(
                candidate_id="scene-1",
                title="Recovered original title",
                release_date=date(2026, 2, 23),
                summary="Candidate summary",
                site_name="Studio",
                performers=["Performer Name"],
                score=0.87,
                source_id="scene-1",
                source_url="https://example.test/scene-1",
            )
        ]

    def fetch_candidate(query, candidate_id):
        assert candidate_id == "scene-1"
        return PluginVideoMetadata(
            title="Recovered original title",
            release_date=date(2026, 2, 23),
            summary="Candidate summary",
            cover_image_path=str(delivered_cover),
            source_id="scene-1",
            source_url="https://example.test/scene-1",
        )

    monkeypatch.setattr(
        VideoMetadataSourceService,
        "sources",
        (
            (
                "demo",
                "Demo",
                {},
                SimpleNamespace(
                    fetch_video=lambda query: None,
                    search_videos=search_videos,
                    fetch_video_candidate=fetch_candidate,
                ),
            ),
        ),
    )
    headers = _auth_headers(client, account_user)

    response = client.post(
        "/videos/metadata/candidates", json={"video_id": video.id}, headers=headers
    )

    assert response.status_code == 200
    body = response.json()
    assert body["relative_path"] == "Studio/Original file.mp4"
    assert body["candidates"][0]["score"] == 0.87
    assert body["candidates"][0]["performers"] == ["Performer Name"]
    candidate_id = body["candidates"][0]["candidate_id"]
    assert queries[0].title == "Original file"

    applied = client.post(
        f"/videos/{video.id}/metadata/apply",
        json={"candidate_id": candidate_id},
        headers=headers,
    )

    assert applied.status_code == 200
    assert applied.json()["title"] == "Recovered original title"
    stored = VideoItem.get_by_id(video.id)
    assert stored.summary == "Candidate summary"
    assert stored.release_date.date() == date(2026, 2, 23)
    assert stored.extra["import_query"]["title"] == "Original file"
    assert stored.extra["metadata_source"]["plugin_id"] == "demo"
    assert stored.cover_image_id != old_cover.id
    assert (image_root / stored.cover_image.origin).is_file()
    assert Image.get_or_none(id=old_cover.id) is None
    assert not old_cover_path.exists()
    assert not delivered_cover.exists()


def test_candidate_cannot_be_applied_to_another_video(
    client, account_user, monkeypatch
):
    first = VideoItem.create(title="First")
    second = VideoItem.create(title="Second")
    monkeypatch.setattr(settings.plugins, "enabled", ["demo"])
    monkeypatch.setattr(
        VideoMetadataSourceService,
        "sources",
        (
            (
                "demo",
                "Demo",
                {},
                SimpleNamespace(
                    fetch_video=lambda query: None,
                    search_videos=lambda query: [
                        PluginVideoCandidate(candidate_id="scene-1", title="Candidate")
                    ],
                    fetch_video_candidate=lambda query, candidate_id: None,
                ),
            ),
        ),
    )
    headers = _auth_headers(client, account_user)
    response = client.post(
        "/videos/metadata/candidates", json={"video_id": first.id}, headers=headers
    )
    candidate_id = response.json()["candidates"][0]["candidate_id"]

    applied = client.post(
        f"/videos/{second.id}/metadata/apply",
        json={"candidate_id": candidate_id},
        headers=headers,
    )

    assert applied.status_code == 422
    assert applied.json()["error"]["code"] == "invalid_video_metadata_candidate"
    assert VideoItem.get_by_id(second.id).title == "Second"


def test_candidate_search_accepts_direct_file_query(client, account_user, monkeypatch):
    monkeypatch.setattr(settings.plugins, "enabled", ["demo"])
    monkeypatch.setattr(
        VideoMetadataSourceService,
        "sources",
        (
            (
                "demo",
                "Demo",
                {},
                SimpleNamespace(
                    fetch_video=lambda query: None,
                    search_videos=lambda query: [],
                    fetch_video_candidate=lambda query, candidate_id: None,
                ),
            ),
        ),
    )

    response = client.post(
        "/videos/metadata/candidates",
        json={"title": "Short title", "relative_path": "Studio/Short title.mp4"},
        headers=_auth_headers(client, account_user),
    )

    assert response.status_code == 200
    assert response.json()["title"] == "Short title"
    assert response.json()["candidates"] == []

    invalid = client.post(
        "/videos/metadata/candidates",
        json={"title": "   "},
        headers=_auth_headers(client, account_user),
    )
    assert invalid.status_code == 422
