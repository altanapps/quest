"""Serve a disposable, credential-free Quest instance for Playwright.

Stage application files (including non-ignored additions and edits) in a temporary
checkout so PROJECT_ROOT-based config/legacy credential lookups cannot reach
the developer's files. No run.py, shared dev-config, containers, or live models.
"""

import argparse
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[2]


def stage_runtime(source: Path, target: Path) -> None:
    tracked = subprocess.check_output(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=source,
    ).decode().split("\0")
    for name in filter(None, tracked):
        path = Path(name)
        if path.parts[0] in {".github", "frontend", "tests", "devplans"}:
            continue
        # These should never be tracked, but protect forks that add config.
        local_configuration = path.name in {
            "server_config.json", "server_credentials.json", "credentials.json",
            "dev_config.json", "dev-config.json", ".env",
        } or path.name.startswith(".env.") or path.name.endswith("_credentials.json")
        if local_configuration or path.parts[0] == "data":
            continue
        original = source / path
        if original.is_file() and not original.is_symlink():
            destination = target / path
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(original, destination)
    shutil.copytree(source / "frontend" / "dist", target / "frontend" / "dist")


def runtime_env(target: Path, port: int) -> dict[str, str]:
    # No inherited provider keys, plugin paths, Python paths, or ADC config.
    env = {
        key: os.environ[key]
        for key in ("PATH", "LANG", "LC_ALL", "TMPDIR", "SYSTEMROOT", "WINDIR")
        if key in os.environ
    }
    home = target / "home"
    home.mkdir()
    env.update({
        "HOME": str(home),
        "USERPROFILE": str(home),
        "PYTHONPATH": str(target),
        "PYTHONUNBUFFERED": "1",
        "QUEST_ENV": "local",
        "QUEST_DATA_DIR": str(target / "data"),
        "QUEST_PORT": str(port),
        "QUEST_SANDBOX_PORT": str(port + 1),
        "QUEST_APP_BASE_URL": f"http://127.0.0.1:{port}",
    })
    return env


def exit_cleanly(signum, frame) -> None:
    # Uvicorn restores and replays signals after graceful shutdown. A Python
    # exit (instead of SIG_DFL) lets TemporaryDirectory's finally block run.
    raise SystemExit(0)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=9360)
    args = parser.parse_args()
    if not 1024 <= args.port < 65535:
        parser.error("--port must leave room for the adjacent sandbox port")
    if not (ROOT / "frontend" / "dist" / "index.html").is_file():
        parser.error("Build the frontend first: npm --prefix frontend run build")

    signal.signal(signal.SIGTERM, exit_cleanly)
    with tempfile.TemporaryDirectory(prefix="quest-e2e-") as directory:
        target = Path(directory)
        stage_runtime(ROOT, target)
        (target / "data").mkdir()
        env = runtime_env(target, args.port)
        os.environ.clear()
        os.environ.update(env)
        os.chdir(target)
        sys.path.insert(0, str(target))
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"], check=True,
        )
        subprocess.run(
            [sys.executable, "scripts/seed_local.py"], check=True,
        )
        # Alice can exercise enabled views; everyone else tests closed gates.
        from config.feature_gates import set_feature_allowed_users, set_feature_enabled
        for feature in ("docs", "tables"):
            set_feature_enabled(feature, True)
            set_feature_allowed_users(feature, ["alice@quest.local"])

        import uvicorn
        uvicorn.run("quest:app", host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
