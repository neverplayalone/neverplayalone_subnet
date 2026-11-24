"""Validator entrypoint: which loop starts in which mode."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from validator import main as main_module


@pytest.fixture
def started(monkeypatch):
    """Run main() against stubbed loops/backend and record what it started."""
    record = SimpleNamespace(light=[], full=[], api=[])
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
