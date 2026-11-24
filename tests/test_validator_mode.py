"""Which mode a validator runs in, and which validator a light one follows."""
from __future__ import annotations

import pytest

from validator import mode


@pytest.fixture
def owner_lookups(monkeypatch):
    """Make the subnet owner hotkey "owner-hk" and count how often it is looked up."""
    calls = []

    def _owner() -> str:
        calls.append(1)
        return "owner-hk"

    monkeypatch.setattr(mode.chain, "subnet_owner_hotkey", _owner)
    return calls


def test_unset_mode_runs_light_and_follows_the_subnet_owner(owner_lookups):
    assert mode.select_mode("", own_hotkey="my-hk") == ("light", "owner-hk")


def test_configured_main_validator_replaces_the_subnet_owner(owner_lookups):
    selected = mode.select_mode("light", own_hotkey="my-hk", main_hotkey="chosen-hk")

    assert selected == ("light", "chosen-hk")
    assert owner_lookups == []


@pytest.mark.parametrize("raw", ["full", "heavy", " Full "])
def test_full_mode_is_selected_by_name_without_touching_the_chain(owner_lookups, raw):
    assert mode.select_mode(raw, own_hotkey="my-hk") == ("full", None)
    assert owner_lookups == []


def test_unset_mode_runs_full_on_the_main_validator_itself(owner_lookups):
    assert mode.select_mode("", own_hotkey="owner-hk") == ("full", "owner-hk")


def test_light_mode_is_refused_on_the_main_validator_itself(owner_lookups):
    with pytest.raises(ValueError, match="NPA_VALIDATOR_MODE=full"):
        mode.select_mode("light", own_hotkey="owner-hk")


def test_unknown_mode_name_is_refused_instead_of_falling_back_to_light(owner_lookups):
    with pytest.raises(ValueError, match="ful"):
        mode.select_mode("ful", own_hotkey="my-hk")
