"""The browser harness must never inherit developer data or credentials."""

import importlib.util
from pathlib import Path
import signal

import pytest


@pytest.fixture
def harness():
    path = Path(__file__).resolve().parents[1] / "frontend" / "e2e" / "server.py"
    spec = importlib.util.spec_from_file_location("e2e_server", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_environment_is_allowlisted_and_uses_disposable_paths(harness, tmp_path, monkeypatch):
    monkeypatch.setenv("QUEST_ENV", "prod")
    monkeypatch.setenv("QUEST_DATA_DIR", "/do-not-use/data")
    monkeypatch.setenv("QUEST_PLUGIN_PATH", "/do-not-use/plugins")
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/do-not-use/adc.json")
    monkeypatch.setenv("GEMINI_VERTEX_PROJECT_ID", "do-not-use")
    monkeypatch.setenv("UNEXPECTED_PROVIDER_KEY", "test-placeholder")
    env = harness.runtime_env(tmp_path, 9360)
    assert env["QUEST_ENV"] == "local"
    assert env["QUEST_DATA_DIR"] == str(tmp_path / "data")
    assert env["HOME"] == str(tmp_path / "home")
    assert env["QUEST_APP_BASE_URL"] == "http://127.0.0.1:9360"
    assert env["QUEST_SANDBOX_PORT"] == "9361"
    assert not {
        "GOOGLE_APPLICATION_CREDENTIALS", "GEMINI_VERTEX_PROJECT_ID",
        "UNEXPECTED_PROVIDER_KEY", "QUEST_PLUGIN_PATH",
    } & env.keys()
    assert (tmp_path / "home").is_dir()


def test_shutdown_unwinds_temporary_directory(harness, tmp_path):
    runtime = tmp_path / "runtime"
    with pytest.raises(SystemExit):
        with harness.tempfile.TemporaryDirectory(dir=tmp_path) as directory:
            runtime = Path(directory)
            (runtime / "quest.db").touch()
            harness.exit_cleanly(signal.SIGTERM, None)
    assert not runtime.exists()


def test_staging_copies_source_and_build_but_not_local_configuration(harness, tmp_path, monkeypatch):
    source = tmp_path / "source"
    target = tmp_path / "runtime"
    names = [
        "quest.py", "chat/routes/example.py", "config/paths.py",
        "server_config.json", "server_credentials.json", "slack_credentials.json",
        "dev-config.json", ".env.local", "data/quest.db", "tests/test_example.py",
        "frontend/src/App.tsx",
    ]
    for name in names + ["frontend/dist/index.html"]:
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("synthetic fixture")
    # Symlinked files must not pull data from outside the source tree.
    (source / "linked.py").symlink_to(source / "server_credentials.json")
    names.append("linked.py")
    monkeypatch.setattr(
        harness.subprocess, "check_output",
        lambda command, cwd: "\0".join(names).encode(),
    )
    harness.stage_runtime(source, target)
    copied = {
        str(path.relative_to(target))
        for path in target.rglob("*") if path.is_file()
    }
    assert copied == {
        "quest.py", "chat/routes/example.py", "config/paths.py",
        "frontend/dist/index.html",
    }
