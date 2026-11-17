import json

from src.config.config import Plugins
from src.plugins.extensions.video_metadata import VIDEO_METADATA_SOURCE_EXTENSION_KEY
from src.plugins.loader import PLUGIN_LOAD_ERRORS, load_enabled_plugins

_REGISTER_SOURCE = """\
from src.plugins import (
    HOST_API_VERSION,
    VIDEO_METADATA_SOURCE_EXTENSION_KEY,
    PluginExtension,
    PluginRegistration,
    PluginVideoMetadataSource,
)


def register(context):
    return PluginRegistration(
        plugin_id="video_metadata",
        display_name="Video metadata",
        version="1.0.0",
        host_api_version=HOST_API_VERSION,
        extensions=(
            PluginExtension(
                key=VIDEO_METADATA_SOURCE_EXTENSION_KEY,
                data=PluginVideoMetadataSource(fetch_video=lambda query: None),
            ),
        ),
    )
"""


def _write_plugin(root, host_api_version):
    plugin = root / "video_metadata"
    plugin.mkdir(parents=True)
    (plugin / "manifest.json").write_text(
        json.dumps(
            {
                "plugin_id": "video_metadata",
                "display_name": "Video metadata",
                "version": "1.0.0",
                "host_api_version": host_api_version,
            }
        ),
        encoding="utf-8",
    )
    (plugin / "__init__.py").write_text(_REGISTER_SOURCE, encoding="utf-8")


def test_loader_accepts_video_metadata_source_on_host_api_19(tmp_path):
    PLUGIN_LOAD_ERRORS.clear()
    _write_plugin(tmp_path, 19)

    loaded = load_enabled_plugins(
        Plugins(enabled=["video_metadata"]), root_dir=tmp_path
    )

    assert len(loaded) == 1
    assert loaded[0].extensions[0].key == VIDEO_METADATA_SOURCE_EXTENSION_KEY
    assert "video_metadata" not in PLUGIN_LOAD_ERRORS


def test_loader_rejects_video_metadata_source_before_host_api_19(tmp_path):
    PLUGIN_LOAD_ERRORS.clear()
    _write_plugin(tmp_path, 18)

    loaded = load_enabled_plugins(
        Plugins(enabled=["video_metadata"]), root_dir=tmp_path
    )

    assert loaded == ()
    assert PLUGIN_LOAD_ERRORS["video_metadata"]["stage"] == "validate_extensions"
