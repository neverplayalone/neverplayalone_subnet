"""Validator mode selection.

``light`` (the default) mirrors the main validator's on-chain weights and runs
no evaluation. ``full`` evaluates miners, uploads scoreboards, computes
consensus and sets its own weights.
"""
from __future__ import annotations

from typing import Optional

from shared import chain

LIGHT = "light"
FULL = "full"
_MODE_NAMES = {"light": LIGHT, "full": FULL, "heavy": FULL}


def select_mode(
    raw_mode: str,
    *,
    own_hotkey: str,
    main_hotkey: str = "",
) -> tuple[str, Optional[str]]:
    """Return ``(mode, main_validator_hotkey)`` for this validator.

    ``raw_mode`` is NPA_VALIDATOR_MODE and ``main_hotkey`` is
    NPA_MAIN_VALIDATOR_HOTKEY; an empty ``main_hotkey`` means the subnet owner's
    hotkey, read from chain. An unset mode is light — except on the main
    validator itself, which has nobody to copy and so keeps evaluating. An
    unknown mode name is an error rather than a silent switch to light.
    """
    name = raw_mode.strip().lower()
    if name and name not in _MODE_NAMES:
        raise ValueError(f"unknown NPA_VALIDATOR_MODE={raw_mode!r}; use 'light' or 'full'")
    requested = _MODE_NAMES.get(name)
    if requested == FULL:
        return FULL, None

    main = main_hotkey.strip() or chain.subnet_owner_hotkey()
    if own_hotkey != main:
        return LIGHT, main
    if requested == LIGHT:
        raise ValueError(
            f"hotkey {own_hotkey} is the main validator and cannot copy itself; "
            "set NPA_VALIDATOR_MODE=full"
        )
    return FULL, main
