"""Light validator: mirror the main validator's on-chain weights.

No evaluation, no backend API — only the chain. Each pass reads the weight row
the main validator has on chain and submits the same row when it changed, and
re-submits it once per weight epoch so this validator stays active.

Commit-reveal means the main validator's row only becomes readable when it is
revealed, and our copy is itself revealed one tempo later, so a light validator
trails the main validator by about one tempo whenever the row changes.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Optional

from shared import chain
from validator.config import LOOP_POLL_SECONDS, WEIGHT_EPOCH_BLOCKS

log = logging.getLogger(__name__)

# Blocks to wait before retrying after the chain did not accept a submission
# (e.g. the weights rate limit right after a restart) or there was nothing to copy.
# Consecutive failed submissions double the wait, up to one weight epoch.
COPY_RETRY_BLOCKS = 10

WeightRow = list[tuple[int, int]]


@dataclass
class CopyState:
    row: Optional[WeightRow] = None  # last row the chain accepted from us
    block: Optional[int] = None  # block of that submission
    retry_block: int = 0  # no attempt before this block
    failures: int = 0  # consecutive submissions the chain did not accept


def _retry_delay(failures: int) -> int:
    """Blocks to wait after the Nth consecutive failed submission: 10, 20, 40, ...
    capped at one weight epoch. A validator that cannot set weights at all (no
    permit, too little stake) therefore settles at one attempt per epoch, the
    same as a full validator, instead of hitting the chain every few blocks."""
    longest = max(WEIGHT_EPOCH_BLOCKS, COPY_RETRY_BLOCKS)
    return min(COPY_RETRY_BLOCKS * 2 ** min(failures - 1, 16), longest)


def copy_step(wallet, state: CopyState, main_hotkey: str, current_block: int) -> bool:
    """Run one copy pass. Returns True when a weight submission was accepted."""
    if current_block < state.retry_block:
        return False

    source = chain.weight_row_for_hotkey(main_hotkey)
    # If the main validator has no readable row (deregistered, or no weights yet),
    # keep re-submitting the last copied row so this validator does not go inactive.
    target = source or state.row
    changed = target != state.row
    refresh_due = state.block is None or current_block - state.block >= WEIGHT_EPOCH_BLOCKS
    if not (changed or refresh_due):
        return False

    if not target:
        log.warning(
            "light: main validator %s has no weights on chain to copy; retrying in %s blocks",
            main_hotkey,
            COPY_RETRY_BLOCKS,
        )
        state.retry_block = current_block + COPY_RETRY_BLOCKS
        return False
    if not source:
        log.warning(
            "light: main validator %s has no weights on chain; re-submitting the last copied row",
            main_hotkey,
        )

    try:
        accepted = chain.set_weight_row(wallet, target)
    except Exception as exc:
        log.warning("light: weight submission failed: %s", exc)
        accepted = False
    if not accepted:
        state.failures += 1
        delay = _retry_delay(state.failures)
        log.warning(
            "light: weights not accepted at block=%s; retrying in %s blocks",
            current_block,
            delay,
        )
        state.retry_block = current_block + delay
        return False

    log.info(
        "light: weights copied from main validator %s block=%s reason=%s row=%s",
        main_hotkey,
        current_block,
        "changed" if changed else "refresh",
        target,
    )
    state.row, state.block, state.failures = target, current_block, 0
    return True


def light_loop(wallet, main_hotkey: str) -> None:
    state = CopyState()
    log.info("light mode started: copying weights of main validator %s", main_hotkey)
    try:
        while True:
            try:
                copy_step(wallet, state, main_hotkey, chain.current_block())
            except Exception as exc:
                log.warning("light: copy pass failed and will retry: %s", exc)
                # Drop the chain client so the next pass opens a fresh connection
                # instead of reusing one that may be dead.
                chain.close_subtensor()
            time.sleep(LOOP_POLL_SECONDS)
    finally:
        chain.close_subtensor()
        log.info("light mode stopped")
