"""Owner-validator helpers for backend bootstrap and round freeze."""
from __future__ import annotations

import logging
import time

from . import chain
from .api_client import APIClient
from .config import FIRST_ROUND_START_AT

log = logging.getLogger(__name__)


def owner_tick(api: APIClient) -> None:
    rounds = api.get_current_rounds()
    submission_round = rounds.get("submission_round")

    if submission_round is None:
        if FIRST_ROUND_START_AT is None:
            return
        try:
            api.admin_bootstrap(int(FIRST_ROUND_START_AT))
            log.info("bootstrapped first round with start_at=%s", FIRST_ROUND_START_AT)
        except Exception as exc:
            log.warning("bootstrap failed: %s", exc)
        return

    if submission_round.get("status") != "submission_open":
        return
    if time.time() < float(submission_round["evaluation_start_at"]):
        return

    try:
        freeze_hash = chain.current_block_hash()
        api.admin_freeze_round(submission_round["round_id"], freeze_hash)
        log.info(
            "froze round=%s at block_hash=%s",
            submission_round["round_id"],
            freeze_hash,
        )
    except Exception as exc:
        log.warning("freeze failed for round=%s: %s", submission_round["round_id"], exc)

