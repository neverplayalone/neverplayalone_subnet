"""Validator entrypoint: which loop starts in which mode."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from validator import main as main_module


@pytest.fixture
def started(monkeypatch, tmp_path):
    """Run main() against stubbed loops/backend and record what it started. The
    repo root is a temp directory, so no test reads or writes the real .env."""
    record = SimpleNamespace(light=[], full=[], api=[])
    record.env_file = tmp_path / ".env"
    monkeypatch.setattr(main_module, "_ROOT", tmp_path)
    wallet = SimpleNamespace(hotkey=SimpleNamespace(ss58_address="my-hk"))
    record.wallet = wallet

    class FakeAPI:
        def __init__(self, wallet, base_url):
            del wallet, base_url
            record.api.append("init")

        def health(self):
            record.api.append("health")
            return {}

        def close(self):
            record.api.append("close")

    monkeypatch.setattr(main_module, "_setup_logging", lambda: None)
    monkeypatch.setattr(main_module.chain, "make_wallet", lambda name, hotkey: wallet)
    monkeypatch.setattr(main_module.chain, "subnet_owner_hotkey", lambda: "owner-hk")
    monkeypatch.setattr(main_module, "APIClient", FakeAPI)
    monkeypatch.setattr(main_module, "light_loop", lambda w, main: record.light.append((w, main)))
    monkeypatch.setattr(main_module, "main_loop", lambda w, api: record.full.append(w))
    monkeypatch.setattr(main_module, "VALIDATOR_MODE", "")
    monkeypatch.setattr(main_module, "MAIN_VALIDATOR_HOTKEY", "")
    return record


def test_default_mode_copies_the_subnet_owner_without_touching_the_backend(started):
    assert main_module.main() == 0

    assert started.light == [(started.wallet, "owner-hk")]
    assert started.full == []
    assert started.api == []


def test_configured_main_validator_is_the_one_copied(started, monkeypatch):
    monkeypatch.setattr(main_module, "MAIN_VALIDATOR_HOTKEY", "chosen-hk")

    assert main_module.main() == 0

    assert started.light == [(started.wallet, "chosen-hk")]


def test_full_mode_checks_the_backend_and_runs_the_evaluation_loop(started, monkeypatch):
    monkeypatch.setattr(main_module, "VALIDATOR_MODE", "full")

    assert main_module.main() == 0

    assert started.full == [started.wallet]
    assert started.light == []
    assert started.api == ["init", "health", "close"]


def test_unknown_mode_exits_with_an_error_and_starts_nothing(started, monkeypatch):
    monkeypatch.setattr(main_module, "VALIDATOR_MODE", "ful")

    assert main_module.main() == 1

    assert started.light == []
    assert started.full == []


def test_exits_with_an_error_when_the_main_validator_cannot_be_resolved(started, monkeypatch):
    def unreachable():
        raise RuntimeError("chain unreachable")

    monkeypatch.setattr(main_module.chain, "subnet_owner_hotkey", unreachable)

    assert main_module.main() == 1

    assert started.light == []
    assert started.full == []


def test_main_validator_with_unset_mode_runs_full_and_records_it_in_dotenv(started, monkeypatch):
    # The scripts read the mode from .env, so the auto-selected mode must land there
    # or they would stop keeping the main validator's npabench current.
    started.env_file.write_text("NPA_WALLET=validator\n")
    monkeypatch.setattr(main_module.chain, "subnet_owner_hotkey", lambda: "my-hk")

    assert main_module.main() == 0

    assert started.full == [started.wallet]
    assert started.light == []
    assert main_module._parse_dotenv(started.env_file) == {
        "NPA_WALLET": "validator",
        "NPA_VALIDATOR_MODE": "full",
    }


def test_main_validator_mode_is_recorded_only_once(started, monkeypatch):
    started.env_file.write_text("NPA_WALLET=validator\n")
    monkeypatch.setattr(main_module.chain, "subnet_owner_hotkey", lambda: "my-hk")

    main_module.main()
    main_module.main()

    assert started.env_file.read_text().count("NPA_VALIDATOR_MODE=full") == 1


def test_main_validator_still_runs_full_when_there_is_no_dotenv_to_record_in(started, monkeypatch):
    monkeypatch.setattr(main_module.chain, "subnet_owner_hotkey", lambda: "my-hk")

    assert main_module.main() == 0

    assert started.full == [started.wallet]
    assert not started.env_file.exists()


def test_light_validator_leaves_dotenv_alone(started):
    started.env_file.write_text("NPA_WALLET=validator\n")

    assert main_module.main() == 0

    assert started.env_file.read_text() == "NPA_WALLET=validator\n"


def test_dotenv_last_assignment_wins_like_the_shell_scripts(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("NPA_TEST_MODE=light\nNPA_TEST_MODE=full\n")
    monkeypatch.delenv("NPA_TEST_MODE", raising=False)

    main_module._load_dotenv(env_file)

    assert main_module.os.environ["NPA_TEST_MODE"] == "full"
    monkeypatch.delenv("NPA_TEST_MODE")


def test_dotenv_accepts_an_export_prefix(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text('export NPA_TEST_MODE="full"\n')
    monkeypatch.delenv("NPA_TEST_MODE", raising=False)

    main_module._load_dotenv(env_file)

    assert main_module.os.environ["NPA_TEST_MODE"] == "full"
    monkeypatch.delenv("NPA_TEST_MODE")


def test_dotenv_reports_settings_the_process_environment_overrides(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("NPA_TEST_MODE=full\nNPA_TEST_SAME=1\n")
    monkeypatch.setenv("NPA_TEST_MODE", "light")  # e.g. the environment pm2 kept from an old start
    monkeypatch.setenv("NPA_TEST_SAME", "1")

    overridden = main_module._load_dotenv(env_file)

    assert overridden == ["NPA_TEST_MODE"]
    assert main_module.os.environ["NPA_TEST_MODE"] == "light"
