"""Drive download preflight and bounded, atomic authenticated streaming.

MockTransport supplies genuine async response streams: using a buffered GET
or reading .content in the download path would break the streaming assertions.
No credentials or upstream services are used.
"""

import asyncio
import gzip
import json
import urllib.parse
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from chat.gemini_api import authed_get
from chat.gemini_api.tool_handlers import drive


USER = {"id": 7, "email": "user@example.com"}
CHUNK = 64 * 1024
LIMIT = 2 * CHUNK
URL = "https://www.googleapis.com/drive/v3/files/file1?alt=media&supportsAllDrives=true"


class Stream(httpx.AsyncByteStream):
    def __init__(self, chunks, failure=None, before_chunk=None):
        self.chunks = chunks
        self.failure = failure
        self.before_chunk = before_chunk
        self.consumed = 0
        self.closed = False

    async def __aiter__(self):
        for chunk in self.chunks:
            if self.before_chunk:
                self.before_chunk()
            self.consumed += 1
            yield chunk
        if self.failure:
            raise self.failure

    async def aclose(self):
        self.closed = True


@pytest.fixture()
def env(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    state = SimpleNamespace(
        workspace=workspace,
        metadata={"name": "report.bin", "size": "5"},
        metadata_status=200,
        metadata_by_id={},
        metadata_status_by_id={},
        stream=Stream([b"hello"]),
        headers={"content-type": "application/octet-stream"},
        status=200,
        requests=[],
        media_statuses=[],
        responses=[],
    )

    def upstream(request):
        state.requests.append(request)
        if request.url.params.get("alt") != "media":
            file_id = request.url.path.rsplit("/", 1)[-1]
            return httpx.Response(
                state.metadata_status_by_id.get(file_id, state.metadata_status),
                json=state.metadata_by_id.get(file_id, state.metadata),
            )
        status = state.media_statuses.pop(0) if state.media_statuses else state.status
        response = httpx.Response(status, headers=state.headers, stream=state.stream)
        state.responses.append(response)
        return response

    real_client = httpx.AsyncClient
    transport = httpx.MockTransport(upstream)
    monkeypatch.setattr(
        authed_get.httpx, "AsyncClient",
        lambda **kwargs: real_client(transport=transport, **kwargs),
    )
    state.loader = AsyncMock(return_value=SimpleNamespace(token="test-only"))
    monkeypatch.setattr(authed_get, "_call_loader", state.loader)
    monkeypatch.setattr(drive, "conversation_workspace_dir", AsyncMock(return_value=workspace))
    monkeypatch.setattr(drive, "_ATTACHMENT_MAX_SIZE_BYTES", LIMIT)
    state.publish = MagicMock()
    monkeypatch.setattr(drive, "_publish_file_list_changed", state.publish)
    return state


def download(env, **kwargs):
    return json.loads(asyncio.run(drive._handle_download_drive_file(
        USER, "conv", "file1", **kwargs,
    )))


def assert_unchanged(env, original=None):
    files = list(env.workspace.iterdir())
    if original is None:
        assert files == []
    else:
        assert files == [env.workspace / "report.bin"]
        assert files[0].read_bytes() == original
    env.publish.assert_not_called()


@pytest.mark.parametrize("filename", [None, "renamed.bin"])
def test_success_and_filename_override(env, filename):
    result = download(env, filename=filename, project_id="project")
    name = filename or "report.bin"
    assert result["status"] == "success"
    assert result["filename"] == name
    assert result["size_bytes"] == 5
    assert result["content_type"] == "application/octet-stream"
    assert (env.workspace / name).read_bytes() == b"hello"
    assert len(env.requests) == 2
    assert env.requests[0].url.params["fields"] == "name,mimeType,size,shortcutDetails(targetId)"
    assert all(r.url.params["supportsAllDrives"] == "true" for r in env.requests)
    assert env.stream.closed
    # A success response must never have been buffered.
    with pytest.raises(httpx.ResponseNotRead):
        _ = env.responses[0].content
    env.publish.assert_called_once_with(USER["id"], "conversation", "conv", "project")
    assert len(list(env.workspace.iterdir())) == 1


@pytest.mark.parametrize("filename", [None, "renamed.bin"])
def test_metadata_cap_cannot_be_bypassed_by_filename_override(env, filename):
    env.metadata["size"] = str(LIMIT + 1)
    result = download(env, filename=filename)
    assert "size limit" in result["error"]
    assert len(env.requests) == 1
    assert env.stream.consumed == 0
    assert_unchanged(env)


@pytest.mark.parametrize("size", [0, LIMIT - 1, LIMIT])
def test_boundary_sizes(env, size):
    env.metadata["size"] = str(size)
    env.stream = Stream([b"a" * min(size, CHUNK), b"b" * max(0, size - CHUNK)])
    result = download(env)
    assert result["size_bytes"] == size
    assert (env.workspace / "report.bin").stat().st_size == size
    assert env.stream.closed


@pytest.mark.parametrize("reported_size", [None, "1", str(LIMIT)])
@pytest.mark.parametrize("existing", [False, True])
def test_stream_cap_ignores_absent_or_underreported_metadata(env, reported_size, existing):
    env.metadata["size"] = reported_size
    original = b"old" if existing else None
    if existing:
        (env.workspace / "report.bin").write_bytes(original)
    env.stream = Stream([b"x" * CHUNK] * 4)
    result = download(env)
    assert "size limit" in result["error"]
    assert env.stream.consumed == 3  # stops without consuming the remainder
    assert env.stream.closed
    assert_unchanged(env, original)


@pytest.mark.parametrize("length", [None, "1", "not-a-number"])
def test_stream_cap_does_not_trust_content_length(env, length):
    if length is not None:
        env.headers["content-length"] = length
    env.stream = Stream([b"x" * CHUNK] * 3)
    assert "size limit" in download(env)["error"]
    assert env.stream.closed
    assert_unchanged(env)


def test_oversized_content_length_rejected_before_reading(env):
    env.headers["content-length"] = str(LIMIT + 1)
    assert "size limit" in download(env)["error"]
    assert env.stream.consumed == 0
    assert env.stream.closed
    assert_unchanged(env)


def test_cap_counts_decoded_bytes(env):
    env.headers["content-encoding"] = "gzip"
    compressed = gzip.compress(b"x" * (LIMIT + 1))
    env.headers["content-length"] = str(len(compressed))
    env.stream = Stream([compressed])
    assert "size limit" in download(env)["error"]
    assert env.stream.closed
    assert_unchanged(env)


@pytest.mark.parametrize("failure", [
    httpx.ReadError("interrupted"),
    httpx.ReadTimeout("timeout"),
    asyncio.CancelledError(),
], ids=["disconnect", "timeout", "cancellation"])
def test_interrupted_download_preserves_destination_and_cleans_temp(env, failure):
    original = b"old report"
    (env.workspace / "report.bin").write_bytes(original)

    def check_atomicity():
        assert (env.workspace / "report.bin").read_bytes() == original
        assert any(p.name.startswith(".drive-download-") for p in env.workspace.iterdir())

    env.stream = Stream([b"x" * CHUNK], failure=failure, before_chunk=check_atomicity)
    if isinstance(failure, asyncio.CancelledError):
        with pytest.raises(asyncio.CancelledError):
            download(env)
    else:
        assert "error" in download(env)
    assert env.stream.closed
    assert_unchanged(env, original)


def test_success_atomically_replaces_existing_file(env):
    (env.workspace / "report.bin").write_bytes(b"old")

    def check_atomicity():
        assert (env.workspace / "report.bin").read_bytes() == b"old"

    env.stream = Stream([b"x" * CHUNK, b"y"], before_chunk=check_atomicity)
    assert download(env)["status"] == "success"
    assert (env.workspace / "report.bin").read_bytes() == b"x" * CHUNK + b"y"
    assert len(list(env.workspace.iterdir())) == 1


def test_oauth_retry_uses_streaming_and_closes_both_responses(env):
    env.media_statuses = [401, 200]
    assert download(env)["status"] == "success"
    assert len(env.responses) == 2
    assert all(r.is_closed for r in env.responses)
    assert env.loader.await_count == 3  # metadata, media, refresh
    assert (env.workspace / "report.bin").read_bytes() == b"hello"


@pytest.mark.parametrize("status", [401, 403, 404, 500, 302])
def test_upstream_failure_leaves_no_file_or_event(env, status):
    env.status = status
    env.stream = Stream([b'{"error": "upstream"}'])
    assert "error" in download(env)
    assert all(r.is_closed for r in env.responses)
    assert_unchanged(env)


def test_large_upstream_errors_are_bounded(env):
    env.status = 500
    env.stream = Stream([b"x" * CHUNK] * 4)
    result = download(env)
    assert len(result["error"]) < CHUNK + 256
    assert env.stream.consumed == 1
    assert env.stream.closed
    assert_unchanged(env)


@pytest.mark.parametrize("metadata", [None, [], "not an object", {"size": "-1"},
                                     {"size": "bad"}, {"size": True},
                                     {"error": "metadata failed"}])
def test_invalid_metadata_rejected_before_media(env, metadata):
    env.metadata = metadata
    assert "error" in download(env)
    assert len(env.requests) == 1
    assert_unchanged(env)


def test_symlink_escape_rejected_before_media(env, tmp_path):
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"outside")
    (env.workspace / "report.bin").symlink_to(outside)
    assert "outside workspace" in download(env)["error"]
    assert outside.read_bytes() == b"outside"
    assert len(env.requests) == 1
    env.publish.assert_not_called()


def test_sanitizes_filename(env):
    result = download(env, filename="folder/report\\file.bin")
    assert result["filename"] == "folder_report_file.bin"
    assert (env.workspace / result["filename"]).read_bytes() == b"hello"


@pytest.mark.parametrize("url", [
    "https://unregistered.example/files",
    "https://www.googleapis.com/drive/v3/files/f/permissions/../x",
    "https://www.googleapis.com/drive/v3/files/f/copy",
])
def test_download_mode_retains_url_and_endpoint_guards(env, url):
    result = asyncio.run(authed_get._make_authed_request(
        url, user=USER, raw_response=True,
        download_to=env.workspace / "report.bin", max_download_bytes=LIMIT,
    ))
    assert "error" in json.loads(result)
    env.loader.assert_not_awaited()
    assert env.requests == []
    assert_unchanged(env)


def test_download_mode_retains_missing_credentials_error(env):
    env.loader.return_value = None
    result = asyncio.run(authed_get._make_authed_request(
        URL, user=USER, raw_response=True,
        download_to=env.workspace / "report.bin", max_download_bytes=LIMIT,
    ))
    assert "google_services_auth_required" in result
    assert env.requests == []
    assert_unchanged(env)


def test_compressed_upstream_error_is_not_decoded_twice(env):
    env.status = 403
    env.headers["content-encoding"] = "gzip"
    env.headers["content-type"] = "application/json"
    env.stream = Stream([gzip.compress(b'{"error": "denied"}')])
    result = download(env)
    assert "'status_code': 403" in result["error"]
    assert "'error': 'denied'" in result["error"]
    assert env.stream.closed
    assert_unchanged(env)


def test_disk_failure_cleans_temp_and_preserves_existing_file(env, monkeypatch):
    original = b"old report"
    (env.workspace / "report.bin").write_bytes(original)

    def fail_replace(*args):
        raise OSError("disk failure")

    monkeypatch.setattr(authed_get.os, "replace", fail_replace)
    assert "disk failure" in download(env)["error"]
    assert env.stream.closed
    assert_unchanged(env, original)


@pytest.mark.parametrize("overrides", [
    {"max_download_bytes": None},
    {"max_download_bytes": 0},
    {"max_download_bytes": -1},
    {"max_download_bytes": True},
    {"max_download_bytes": "100"},
    {"download_to": None},
    {"raw_response": False},
    {"method": "POST"},
])
def test_invalid_download_options_rejected_before_io(env, overrides):
    kwargs = {
        "user": USER, "raw_response": True,
        "download_to": env.workspace / "report.bin", "max_download_bytes": LIMIT,
        **overrides,
    }
    result = asyncio.run(authed_get._make_authed_request(URL, **kwargs))
    assert "Downloads require" in json.loads(result)["error"]
    env.loader.assert_not_awaited()
    assert env.requests == []
    assert_unchanged(env)


def shortcut(target_id):
    return {
        "name": "Shortcut alias",
        "mimeType": "application/vnd.google-apps.shortcut",
        "shortcutDetails": {"targetId": target_id, "targetMimeType": "application/pdf"},
    }


def setup_shortcut(env, target_metadata=None):
    env.metadata = shortcut("target1")
    env.metadata_by_id["target1"] = target_metadata if target_metadata is not None else {
        "name": "target.pdf", "mimeType": "application/pdf", "size": "5",
    }


@pytest.mark.parametrize("filename", [None, "override.pdf"])
@pytest.mark.parametrize("project_id", [None, "project"])
def test_shortcut_downloads_target_bytes_and_uses_target_name(env, filename, project_id):
    setup_shortcut(env)
    result = download(env, filename=filename, project_id=project_id)
    name = filename or "target.pdf"
    assert result["status"] == "success"
    assert result["filename"] == name
    assert result["size_bytes"] == 5
    assert (env.workspace / name).read_bytes() == b"hello"
    assert [r.url.path.rsplit("/", 1)[-1] for r in env.requests] == [
        "file1", "target1", "target1",
    ]
    assert env.requests[-1].url.params["alt"] == "media"
    assert all(r.url.params["supportsAllDrives"] == "true" for r in env.requests)
    env.publish.assert_called_once_with(USER["id"], "conversation", "conv", project_id)


def test_shortcut_does_not_trust_cached_target_mime_or_size(env):
    setup_shortcut(env)
    env.metadata["shortcutDetails"]["targetMimeType"] = "application/vnd.google-apps.document"
    env.metadata["size"] = str(LIMIT + 1)  # only the resolved file's size matters
    assert download(env)["status"] == "success"
    assert (env.workspace / "target.pdf").read_bytes() == b"hello"


@pytest.mark.parametrize("filename", [None, "override.pdf"])
def test_shortcut_target_is_size_checked_even_with_override(env, filename):
    setup_shortcut(env, {"name": "large.pdf", "mimeType": "application/pdf", "size": str(LIMIT + 1)})
    assert "size limit" in download(env, filename=filename)["error"]
    assert len(env.requests) == 2
    assert env.stream.consumed == 0
    assert_unchanged(env)


def test_shortcut_target_still_has_streaming_cap(env):
    setup_shortcut(env)
    env.metadata_by_id["target1"].pop("size")
    env.stream = Stream([b"x" * CHUNK] * 4)
    assert "size limit" in download(env)["error"]
    assert env.stream.consumed == 3
    assert env.stream.closed
    assert_unchanged(env)


@pytest.mark.parametrize("kind,tool,id_param", [
    ("document", "google_export_doc", "document_id"),
    ("spreadsheet", "google_export_sheet", "spreadsheet_id"),
    ("presentation", "google_export_slides", "presentation_id"),
])
@pytest.mark.parametrize("via_shortcut", [False, True])
@pytest.mark.parametrize("filename", [None, "override.pdf"])
def test_native_files_point_to_correct_export_tool_and_resolved_id(
    env, kind, tool, id_param, via_shortcut, filename,
):
    metadata = {"name": "Native file", "mimeType": f"application/vnd.google-apps.{kind}"}
    if via_shortcut:
        setup_shortcut(env, metadata)
    else:
        env.metadata = metadata
    result = download(env, filename=filename)
    assert "no binary content" in result["error"]
    assert f'tool_name="{tool}"' in result["hint"]
    arguments = {id_param: "target1" if via_shortcut else "file1", "format": "pdf"}
    assert json.dumps(arguments) in result["hint"]
    assert len(env.requests) == (2 if via_shortcut else 1)
    assert not any(r.url.params.get("alt") == "media" for r in env.requests)
    drive.conversation_workspace_dir.assert_not_awaited()
    assert_unchanged(env)


@pytest.mark.parametrize("via_shortcut", [False, True])
def test_folders_return_listing_guidance_not_a_download(env, via_shortcut):
    metadata = {"name": "Folder", "mimeType": "application/vnd.google-apps.folder"}
    if via_shortcut:
        setup_shortcut(env, metadata)
    else:
        env.metadata = metadata
    result = download(env)
    assert "folders" in result["error"]
    assert "authed_get" in result["hint"]
    url = result["hint"].split(": ", 1)[1]
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
    target_id = "target1" if via_shortcut else "file1"
    assert query["q"] == [f"'{target_id}' in parents and trashed = false"]
    assert query["supportsAllDrives"] == ["true"]
    assert query["includeItemsFromAllDrives"] == ["true"]
    assert not any(r.url.params.get("alt") == "media" for r in env.requests)
    drive.conversation_workspace_dir.assert_not_awaited()
    assert_unchanged(env)


@pytest.mark.parametrize("kind", ["form", "drawing", "drive-sdk", "unknown"])
@pytest.mark.parametrize("via_shortcut", [False, True])
def test_other_workspace_types_fail_without_suggesting_wrong_export(env, kind, via_shortcut):
    metadata = {"mimeType": f"application/vnd.google-apps.{kind}"}
    if via_shortcut:
        setup_shortcut(env, metadata)
    else:
        env.metadata = metadata
    result = download(env)
    assert "not supported" in result["error"]
    assert "native application" in result["hint"]
    assert "google_export_" not in result["hint"]
    assert not any(r.url.params.get("alt") == "media" for r in env.requests)
    assert_unchanged(env)


@pytest.mark.parametrize("details", [
    None, [], "bad", {}, {"targetId": None}, {"targetId": 1},
    {"targetId": ""}, {"targetId": "../escape"},
    {"targetId": "target/permissions"}, {"targetId": "target?alt=media"},
    {"targetId": "target#fragment"}, {"targetId": "target%2Fpermissions"},
    {"targetId": "https://example.com"}, {"targetId": "target\n"},
])
def test_missing_or_malformed_shortcut_target_is_rejected(env, details):
    env.metadata = {"mimeType": "application/vnd.google-apps.shortcut", "shortcutDetails": details}
    assert "targetId" in download(env)["error"]
    assert len(env.requests) == 1
    assert_unchanged(env)


@pytest.mark.parametrize("file_id", [None, 12, "", "../file", "file?alt=media", "https://example.com", "file\n"])
def test_invalid_file_ids_are_rejected_without_io(env, file_id):
    result = json.loads(asyncio.run(drive._handle_download_drive_file(USER, "conv", file_id)))
    assert "file_id" in result["error"]
    assert env.requests == []
    env.loader.assert_not_awaited()
    assert_unchanged(env)


@pytest.mark.parametrize("self_reference", [False, True])
def test_shortcut_cycles_stop_without_repeating_requests(env, self_reference):
    env.metadata = shortcut("file1" if self_reference else "target1")
    env.metadata_by_id["target1"] = shortcut("file1")
    assert "cycle" in download(env)["error"]
    assert len(env.requests) == (1 if self_reference else 2)
    assert_unchanged(env)


def test_shortcut_resolution_has_bounded_metadata_requests(env):
    env.metadata = shortcut("target1")
    for i in range(1, drive._MAX_DRIVE_SHORTCUT_HOPS + 1):
        env.metadata_by_id[f"target{i}"] = shortcut(f"target{i + 1}")
    assert "resolution limit" in download(env)["error"]
    assert len(env.requests) == drive._MAX_DRIVE_SHORTCUT_HOPS + 1
    assert_unchanged(env)


def test_target_at_hop_limit_still_downloads(env):
    env.metadata = shortcut("target1")
    for i in range(1, drive._MAX_DRIVE_SHORTCUT_HOPS):
        env.metadata_by_id[f"target{i}"] = shortcut(f"target{i + 1}")
    env.metadata_by_id[f"target{drive._MAX_DRIVE_SHORTCUT_HOPS}"] = {
        "name": "last.pdf", "mimeType": "application/pdf", "size": "5",
    }
    assert download(env)["status"] == "success"
    assert (env.workspace / "last.pdf").read_bytes() == b"hello"
    assert len(env.requests) == drive._MAX_DRIVE_SHORTCUT_HOPS + 2


@pytest.mark.parametrize("status", [403, 404])
def test_broken_or_inaccessible_target_returns_actionable_error(env, status):
    setup_shortcut(env, {"error": {"message": "Target unavailable"}})
    env.metadata_status_by_id["target1"] = status
    result = download(env)
    assert "shortcut target metadata" in result["error"]
    assert "Target unavailable" in result["error"]
    assert "connected Google account" in result["hint"]
    assert len(env.requests) == 2
    assert_unchanged(env)


@pytest.mark.parametrize("metadata", [None, [], "invalid", {"mimeType": 42}, {"size": "bad"}])
def test_malformed_target_metadata_is_rejected_before_media(env, metadata):
    setup_shortcut(env)
    env.metadata_by_id["target1"] = metadata
    assert "error" in download(env)
    assert len(env.requests) == 2
    assert_unchanged(env)
