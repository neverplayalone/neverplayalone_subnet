"""validator_setup.sh / validator_autoupdate.sh: what each validator mode installs.

The scripts run for real against a throwaway repo root; every tool that would
touch the machine (sudo, docker, git, uv, npm, pm2, ...) is replaced by a stub
that only records how it was called.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
BENCH_URL = "https://github.com/neverplayalone/neverplayalone_bench"

STUB = r"""#!/usr/bin/env bash
name="$(basename "$0")"
echo "$name $*" >> "$STUB_LOG"
case "$name" in
  node) echo "v20.11.0" ;;
  git)
    case " $* " in
      *" clone "*) mkdir -p "${@: -1}/.git" "${@: -1}/tools/recorder" ;;
      *" rev-parse "*) echo "abc1234" ;;
    esac
    ;;
  uv) if [[ "${1:-}" == "venv" ]]; then mkdir -p "$2"; fi ;;
  sleep) kill -TERM "$PPID" ;;  # end the updater's endless loop after one pass
esac
exit 0
"""
STUBBED = ("sudo", "docker", "git", "uv", "npm", "npx", "pm2", "node", "curl", "sleep")


class Sandbox:
    def __init__(self, tmp_path: Path):
        self.root = tmp_path / "repo"
        self.bench = self.root / "vendor" / "neverplayalone_bench"
        self.log = tmp_path / "calls.log"
        self.lock = tmp_path / "updater.lock"
        self._bin = tmp_path / "bin"
        (self.root / "scripts").mkdir(parents=True)
        for script in ("validator_setup.sh", "validator_autoupdate.sh", "validator_update.sh"):
            shutil.copy(REPO_ROOT / "scripts" / script, self.root / "scripts" / script)
        shutil.copy(REPO_ROOT / ".env.example", self.root / ".env.example")
        self._bin.mkdir()
        for name in STUBBED:
            stub = self._bin / name
            stub.write_text(STUB)
            stub.chmod(0o755)
        self.log.write_text("")

    def run(self, script: str, *args: str, env: dict[str, str] | None = None) -> str:
        """Run a script and return its combined output."""
        result = subprocess.run(
            ["bash", str(self.root / "scripts" / script), *args],
            env={
                "PATH": f"{self._bin}:/usr/bin:/bin",
                "HOME": str(self.root.parent),
                "STUB_LOG": str(self.log),
                "NPA_UPDATE_LOCK_FILE": str(self.lock),
                **(env or {}),
            },
            capture_output=True,
            text=True,
            timeout=60,
        )
        return result.stdout + result.stderr

    def calls(self) -> str:
        return self.log.read_text()

    def env_file(self) -> str:
        return (self.root / ".env").read_text()

    def add_bench_checkout(self) -> None:
        (self.bench / ".git").mkdir(parents=True)
        (self.bench / "tools" / "recorder").mkdir(parents=True)


@pytest.fixture
def sandbox(tmp_path):
    return Sandbox(tmp_path)


def test_setup_is_light_by_default_and_installs_no_evaluation_stack(sandbox):
    sandbox.run("validator_setup.sh")

    calls = sandbox.calls()
    assert f"uv pip install -e {sandbox.root}\n" in calls
    assert "docker" not in calls
    assert "git clone" not in calls
    assert not sandbox.bench.exists()
    assert "NPA_VALIDATOR_MODE=light\n" in sandbox.env_file()


def test_setup_with_full_flag_installs_the_evaluation_stack(sandbox):
    sandbox.run("validator_setup.sh", "--full")

    calls = sandbox.calls()
    assert "docker info\n" in calls
    assert f"git clone {BENCH_URL} {sandbox.bench}\n" in calls
    assert f"uv pip install -e {sandbox.bench}\n" in calls
    assert "npm install\n" in calls  # recorder dependencies
    assert "NPA_VALIDATOR_MODE=full\n" in sandbox.env_file()


@pytest.mark.parametrize("mode", ["full", "heavy", "FULL"])
def test_setup_installs_the_evaluation_stack_when_the_environment_selects_full(sandbox, mode):
    sandbox.run("validator_setup.sh", env={"NPA_VALIDATOR_MODE": mode})

    assert f"git clone {BENCH_URL} {sandbox.bench}\n" in sandbox.calls()


def test_setup_installs_the_evaluation_stack_when_dotenv_selects_full(sandbox):
    (sandbox.root / ".env").write_text("NPA_WALLET=validator\nNPA_VALIDATOR_MODE=full\n")

    sandbox.run("validator_setup.sh")

    assert f"git clone {BENCH_URL} {sandbox.bench}\n" in sandbox.calls()
    assert sandbox.env_file() == "NPA_WALLET=validator\nNPA_VALIDATOR_MODE=full\n"


def test_setup_keeps_an_existing_bench_checkout_current_when_no_mode_is_set(sandbox):
    sandbox.add_bench_checkout()

    sandbox.run("validator_setup.sh")

    calls = sandbox.calls()
    assert f"git -C {sandbox.bench} fetch --tags origin\n" in calls
    assert f"uv pip install -e {sandbox.bench}\n" in calls


def test_updater_does_not_look_for_a_bench_checkout_on_a_light_validator(sandbox):
    output = sandbox.run("validator_autoupdate.sh")

    assert "no updates detected" in output
    assert "bench checkout missing" not in output


def test_updater_reports_a_missing_bench_checkout_on_a_full_validator(sandbox):
    output = sandbox.run("validator_autoupdate.sh", env={"NPA_VALIDATOR_MODE": "full"})

    assert "bench checkout missing" in output
