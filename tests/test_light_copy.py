"""Light validator: mirror the main validator's on-chain weight row."""
from __future__ import annotations

import pytest

from validator import light

WINNER_ROW = [(241, 65535)]
NEXT_WINNER_ROW = [(0, 65535), (197, 3449)]


class ChainStub:
    """What the main validator has on chain, and every row we were asked to submit."""

    def __init__(self):
        self.main_row = WINNER_ROW
        self.accept = True
        self.submitted: list[list[tuple[int, int]]] = []
        self.read_hotkeys: list[str] = []

    def weight_row_for_hotkey(self, hotkey):
        self.read_hotkeys.append(hotkey)
        return self.main_row

    def set_weight_row(self, wallet, row):
        del wallet
        self.submitted.append(row)
        return self.accept


@pytest.fixture
def chain_stub(monkeypatch):
    stub = ChainStub()
    monkeypatch.setattr(light.chain, "weight_row_for_hotkey", stub.weight_row_for_hotkey)
    monkeypatch.setattr(light.chain, "set_weight_row", stub.set_weight_row)
    monkeypatch.setattr(light, "WEIGHT_EPOCH_BLOCKS", 360)
    monkeypatch.setattr(light, "COPY_RETRY_BLOCKS", 10)
    return stub


def _step(state, block):
    return light.copy_step(object(), state, "main-hk", block)


def test_first_step_copies_the_main_validators_row(chain_stub):
    assert _step(light.CopyState(), 1000) is True

    assert chain_stub.read_hotkeys == ["main-hk"]
    assert chain_stub.submitted == [WINNER_ROW]


def test_unchanged_row_is_not_resubmitted_within_a_weight_epoch(chain_stub):
    state = light.CopyState()
    _step(state, 1000)

    assert _step(state, 1359) is False

    assert chain_stub.submitted == [WINNER_ROW]


def test_unchanged_row_is_resubmitted_once_a_weight_epoch_has_passed(chain_stub):
    state = light.CopyState()
    _step(state, 1000)

    assert _step(state, 1360) is True

    assert chain_stub.submitted == [WINNER_ROW, WINNER_ROW]


def test_changed_row_is_copied_on_the_next_step(chain_stub):
    state = light.CopyState()
    _step(state, 1000)
    chain_stub.main_row = NEXT_WINNER_ROW

    assert _step(state, 1001) is True

    assert chain_stub.submitted == [WINNER_ROW, NEXT_WINNER_ROW]


def test_rejected_submission_is_retried_after_the_retry_delay(chain_stub):
    state = light.CopyState()
    chain_stub.accept = False
    assert _step(state, 1000) is False

    chain_stub.accept = True
    assert _step(state, 1009) is False  # still inside the retry delay
    assert chain_stub.submitted == [WINNER_ROW]

    assert _step(state, 1010) is True
    assert chain_stub.submitted == [WINNER_ROW, WINNER_ROW]
    assert _step(state, 1011) is False  # accepted: nothing left to do


@pytest.mark.parametrize("missing", [None, []])
def test_last_copied_row_is_kept_fresh_when_the_main_row_disappears(chain_stub, missing):
    state = light.CopyState()
    _step(state, 1000)
    chain_stub.main_row = missing

    assert _step(state, 1001) is False  # nothing new to copy
    assert _step(state, 1360) is True

    assert chain_stub.submitted == [WINNER_ROW, WINNER_ROW]


@pytest.mark.parametrize("missing", [None, []])
def test_nothing_is_submitted_when_there_has_never_been_a_row_to_copy(chain_stub, missing):
    chain_stub.main_row = missing

    assert _step(light.CopyState(), 1000) is False

    assert chain_stub.submitted == []


def test_light_loop_keeps_copying_after_a_chain_error(chain_stub, monkeypatch):
    class StopLoop(BaseException):
        """Ends the endless loop the way an operator interrupt would."""

    blocks = iter([RuntimeError("rpc down"), 1000])

    def current_block():
        value = next(blocks)
        if isinstance(value, Exception):
            raise value
        return value

    sleeps = []

    def sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) == 2:  # one failed pass, one successful pass
            raise StopLoop

    closed = []
    monkeypatch.setattr(light.chain, "current_block", current_block)
    monkeypatch.setattr(light.chain, "close_subtensor", lambda: closed.append(1))
    monkeypatch.setattr(light.time, "sleep", sleep)

    with pytest.raises(StopLoop):
        light.light_loop(object(), "main-hk")

    assert chain_stub.submitted == [WINNER_ROW]
    assert closed == [1]  # the chain client is released when the loop exits
