"""validator_setup.sh / validator_autoupdate.sh: what each validator mode installs.

The scripts run for real against a throwaway repo root; every tool that would
touch the machine (sudo, docker, git, uv, npm, pm2, ...) is replaced by a stub
that only records how it was called.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
BENCH_URL = "https://github.com/neverplayalone/neverplayalone_bench"

STUB = r"""#!/usr/bin/env bash
name="$(basename "$0")"
echo "$name $*" >> "$STUB_LOG"
# STUB_FAIL_ONCE=<tool>: that tool fails the first time it is called.
if [[ "${STUB_FAIL_ONCE:-}" == "$name" && ! -e "$STUB_LOG.failed" ]]; then
  : > "$STUB_LOG.failed"
  exit 1
fi
case "$name" in
  node) echo "v20.11.0" ;;
  git)
    case " $* " in
      *" clone "*) mkdir -p "${@: -1}/.git" "${@: -1}/tools/recorder" ;;
      *" pull "*) : > "$STUB_LOG.pulled" ;;
      *" rev-parse "*)
        # STUB_GIT_REMOTE=<sha>: origin is ahead of HEAD until `git pull` has run.
        if [[ -n "${STUB_GIT_REMOTE:-}" && ! -e "$STUB_LOG.pulled" && "${@: -1}" != "HEAD" ]]; then
          echo "$STUB_GIT_REMOTE"
        else
          echo "abc1234"
        fi
        ;;
    esac
    ;;
  uv) if [[ "${1:-}" == "venv" ]]; then mkdir -p "$2"; fi ;;
  sleep)
    # The updater loops forever: let it run STUB_PASSES passes (default 1), running
    # STUB_BETWEEN_PASSES between them, then end it.
    passes=$(( $(cat "$STUB_LOG.sleeps" 2>/dev/null || echo 0) + 1 ))
    echo "$passes" > "$STUB_LOG.sleeps"
    if (( passes < ${STUB_PASSES:-1} )); then
      eval "${STUB_BETWEEN_PASSES:-:}"
    else
      kill -TERM "$PPID"
    fi
    ;;
esac
exit 0
"""
# Stands in for the venv python that answers "are we just before a round start?".
IN_WINDOW_PYTHON = "#!/usr/bin/env bash\ncat >/dev/null\necho inside_prestart_window\n"
STUBBED = ("sudo", "docker", "git", "uv", "npm", "npx", "pm2", "node", "curl", "sleep")


GIT_ENV = {
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_SYSTEM": "/dev/null",
    "GIT_AUTHOR_NAME": "test",
    "GIT_AUTHOR_EMAIL": "test@example.invalid",
    "GIT_COMMITTER_NAME": "test",
    "GIT_COMMITTER_EMAIL": "test@example.invalid",
}


def git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        env={"PATH": "/usr/bin:/bin", **GIT_ENV},
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


class Sandbox:
    def __init__(self, tmp_path: Path, *, real_git: bool = False):
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
            if real_git and name == "git":
                continue
            stub = self._bin / name
            stub.write_text(STUB)
            stub.chmod(0o755)
        self.log.write_text("")

    def run(self, script: str, *args: str, env: dict[str, str] | None = None) -> str:
        """Run a script and return its combined output."""
        self.returncode = None
        result = subprocess.run(
            ["bash", str(self.root / "scripts" / script), *args],
            env={
                "PATH": f"{self._bin}:/usr/bin:/bin",
                "HOME": str(self.root.parent),
                "STUB_LOG": str(self.log),
                "NPA_UPDATE_LOCK_FILE": str(self.lock),
                **GIT_ENV,
                **(env or {}),
            },
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.returncode = result.returncode
        return result.stdout + result.stderr

    def open_prestart_window(self) -> None:
        """Make the updater believe a round is about to start, so it applies updates."""
        python = self.root / ".venv" / "bin" / "python"
        python.parent.mkdir(parents=True, exist_ok=True)
        python.write_text(IN_WINDOW_PYTHON)
        python.chmod(0o755)
        if not (self.root / ".env").exists():
            (self.root / ".env").write_text("")

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


def test_light_setup_leaves_an_existing_bench_checkout_alone(sandbox):
    sandbox.add_bench_checkout()

    sandbox.run("validator_setup.sh")

    calls = sandbox.calls()
    assert f"uv pip install -e {sandbox.root}\n" in calls
    assert str(sandbox.bench) not in calls
    assert "docker" not in calls
    assert "npm install\n" not in calls  # recorder dependencies


def test_full_setup_keeps_an_existing_bench_checkout_current(sandbox):
    sandbox.add_bench_checkout()

    sandbox.run("validator_setup.sh", env={"NPA_VALIDATOR_MODE": "full"})

    calls = sandbox.calls()
    assert f"git -C {sandbox.bench} fetch --tags origin\n" in calls
    assert f"uv pip install -e {sandbox.bench}\n" in calls


def test_updater_does_not_look_for_a_bench_checkout_on_a_light_validator(sandbox):
    output = sandbox.run("validator_autoupdate.sh")

    assert "no updates detected" in output
    assert "bench checkout missing" not in output


def test_updater_ignores_an_existing_bench_checkout_on_a_light_validator(sandbox):
    sandbox.add_bench_checkout()

    output = sandbox.run("validator_autoupdate.sh")

    assert "no updates detected" in output
    assert str(sandbox.bench) not in sandbox.calls()


def test_updater_tracks_the_bench_checkout_on_a_full_validator(sandbox):
    sandbox.add_bench_checkout()

    sandbox.run("validator_autoupdate.sh", env={"NPA_VALIDATOR_MODE": "full"})

    assert f"git -C {sandbox.bench} fetch --prune --tags origin\n" in sandbox.calls()


def test_updater_reports_a_missing_bench_checkout_on_a_full_validator(sandbox):
    output = sandbox.run("validator_autoupdate.sh", env={"NPA_VALIDATOR_MODE": "full"})

    assert "bench checkout missing" in output


FULL = {"NPA_VALIDATOR_MODE": "full"}


class BenchUpstream:
    """A local stand-in for the npabench GitHub repo, plus a validator's vendored
    checkout of it that was first installed at release "v1"."""

    def __init__(self, tmp_path: Path):
        self.sandbox = Sandbox(tmp_path, real_git=True)
        self.origin = tmp_path / "bench-origin"
        self.checkout = self.sandbox.bench
        self.origin.mkdir()
        git(self.origin, "init", "--quiet", "--initial-branch", "main")
        self.release("v1")
        self.checkout.parent.mkdir(parents=True)
        git(self.checkout.parent, "clone", "--quiet", str(self.origin), self.checkout.name)

    def release(self, version: str, *, new_file: str | None = None) -> str:
        """Publish a new upstream commit and return its sha."""
        (self.origin / "tools" / "recorder").mkdir(parents=True, exist_ok=True)
        (self.origin / "tools" / "recorder" / "package.json").write_text("{}\n")
        (self.origin / "missions.txt").write_text(f"missions as of {version}\n")
        if new_file:
            (self.origin / new_file).parent.mkdir(parents=True, exist_ok=True)
            (self.origin / new_file).write_text(f"added in {version}\n")
        git(self.origin, "add", "--all")
        git(self.origin, "commit", "--quiet", "--message", version)
        return git(self.origin, "rev-parse", "HEAD")

    def sync_checkout(self) -> None:
        """What every earlier successful update did."""
        git(self.checkout, "fetch", "--quiet", "origin")
        git(self.checkout, "checkout", "--quiet", "--detach", "origin/main")

    def checkout_head(self) -> str:
        return git(self.checkout, "rev-parse", "HEAD")


@pytest.fixture
def bench(tmp_path):
    return BenchUpstream(tmp_path)


def test_setup_updates_a_bench_checkout_that_has_a_locally_edited_file(bench):
    (bench.checkout / "missions.txt").write_text("edited on the validator\n")
    latest = bench.release("v2")

    bench.sandbox.run("validator_setup.sh", env=FULL)

    assert bench.checkout_head() == latest
    assert (bench.checkout / "missions.txt").read_text() == "missions as of v2\n"


def test_setup_does_not_roll_the_bench_back_when_a_stray_file_blocks_the_update(bench):
    bench.release("v2")
    bench.sync_checkout()
    (bench.checkout / "new_mission.txt").write_text("left behind on the validator\n")
    latest = bench.release("v3", new_file="new_mission.txt")

    bench.sandbox.run("validator_setup.sh", env=FULL)

    assert bench.checkout_head() == latest
    assert (bench.checkout / "missions.txt").read_text() == "missions as of v3\n"
    assert (bench.checkout / "new_mission.txt").read_text() == "added in v3\n"


def test_setup_fails_instead_of_reporting_success_when_the_bench_cannot_be_updated(bench):
    installed = bench.checkout_head()
    bench.release("v2")
    (bench.checkout / ".git" / "index.lock").write_text("")  # a crashed git left its lock

    output = bench.sandbox.run("validator_setup.sh", env=FULL)

    assert bench.sandbox.returncode != 0
    assert bench.checkout_head() == installed
    assert "setup complete" not in output


def test_setup_checks_out_a_pinned_bench_commit(bench):
    pinned = bench.release("v2")
    bench.release("v3")

    bench.sandbox.run("validator_setup.sh", env={**FULL, "NPA_BENCH_REF": pinned})

    assert bench.checkout_head() == pinned


def test_setup_refuses_a_bench_ref_that_only_exists_as_a_stale_local_branch(bench):
    installed = bench.checkout_head()
    git(bench.checkout, "branch", "retired")  # a branch that is gone from origin
    bench.release("v2")

    output = bench.sandbox.run("validator_setup.sh", env={**FULL, "NPA_BENCH_REF": "retired"})

    assert bench.sandbox.returncode != 0
    assert "setup complete" not in output
    assert bench.checkout_head() == installed


@pytest.mark.parametrize(
    "dotenv",
    [
        "NPA_WALLET=validator\nNPA_VALIDATOR_MODE=light\n",  # written by an earlier light setup
        "NPA_WALLET=validator\n",  # a validator installed before modes existed
    ],
)
def test_full_flag_makes_an_existing_validator_full_for_good(sandbox, dotenv):
    (sandbox.root / ".env").write_text(dotenv)

    sandbox.run("validator_setup.sh", "--full")

    lines = sandbox.env_file().splitlines()
    assert "NPA_WALLET=validator" in lines
    assert [line for line in lines if "NPA_VALIDATOR_MODE" in line] == ["NPA_VALIDATOR_MODE=full"]


@pytest.mark.parametrize(
    "dotenv",
    [
        "NPA_VALIDATOR_MODE=light\nNPA_VALIDATOR_MODE=full\n",  # appended: the last line wins
        "export NPA_VALIDATOR_MODE=full\n",
        "NPA_VALIDATOR_MODE=full\r\n",  # edited on Windows
        'NPA_VALIDATOR_MODE="full"\n',
    ],
)
def test_setup_reads_the_mode_from_dotenv_like_the_other_scripts(sandbox, dotenv):
    (sandbox.root / ".env").write_bytes(dotenv.encode())

    sandbox.run("validator_setup.sh")

    assert f"git clone {BENCH_URL} {sandbox.bench}\n" in sandbox.calls()


def test_setup_stops_on_an_unknown_mode_instead_of_installing_light(sandbox):
    output = sandbox.run("validator_setup.sh", env={"NPA_VALIDATOR_MODE": "ful"})

    assert sandbox.returncode != 0
    assert "ful" in output
    assert "uv pip install" not in sandbox.calls()


def test_updater_follows_a_mode_change_in_dotenv_without_being_restarted(sandbox):
    sandbox.add_bench_checkout()
    (sandbox.root / ".env").write_text("NPA_VALIDATOR_MODE=light\n")

    sandbox.run(
        "validator_autoupdate.sh",
        env={
            "STUB_PASSES": "2",
            "STUB_BETWEEN_PASSES": f"echo NPA_VALIDATOR_MODE=full >> {sandbox.root}/.env",
        },
    )

    assert f"git -C {sandbox.bench} fetch --prune --tags origin\n" in sandbox.calls()


def test_updater_reports_a_failed_update_instead_of_claiming_success(sandbox):
    sandbox.open_prestart_window()

    output = sandbox.run(
        "validator_autoupdate.sh",
        env={"STUB_GIT_REMOTE": "def5678", "STUB_FAIL_ONCE": "uv"},
    )

    assert "update attempt failed" in output
    assert "validator update complete" not in output
    assert "pm2 restart" not in sandbox.calls()


def test_updater_retries_a_failed_update_even_though_the_pull_already_happened(sandbox):
    # validator_update.sh pulls before it runs setup, so after a failed setup there is
    # no drift left; without a retry the validator would stay on its old code forever.
    sandbox.open_prestart_window()

    output = sandbox.run(
        "validator_autoupdate.sh",
        env={"STUB_GIT_REMOTE": "def5678", "STUB_FAIL_ONCE": "uv", "STUB_PASSES": "2"},
    )

    assert "update attempt failed" in output
    assert "validator update complete" in output
    assert "pm2 restart validator --update-env\n" in sandbox.calls()


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores the directory permissions used here")
def test_updater_does_not_roll_the_bench_back_when_its_checkout_fails(bench):
    installed = bench.checkout_head()
    bench.release("v2")
    bench.sync_checkout()
    blocked = bench.checkout / "generated"
    blocked.mkdir()
    blocked.chmod(0o555)  # e.g. a directory a container created as another user
    bench.release("v3", new_file="generated/report.txt")
    (bench.sandbox.root / ".env").write_text("NPA_VALIDATOR_MODE=full\n")
    bench.sandbox.open_prestart_window()

    try:
        output = bench.sandbox.run("validator_autoupdate.sh")
    finally:
        if blocked.exists():
            blocked.chmod(0o755)

    assert "bench sync failed" in output
    assert bench.checkout_head() != installed
    assert "pm2 restart" not in bench.sandbox.calls()
