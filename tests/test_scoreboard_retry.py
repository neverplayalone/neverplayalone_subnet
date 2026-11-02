from types import SimpleNamespace

import pytest

from validator import loop


@pytest.mark.parametrize("expire", [False, True])
def test_completed_evaluation_survives_upload_failure_in_ram(monkeypatch, expire):
    state = {
        "round_id": "2026-10-31",
        "evaluation_start_block": 100,
        "scoreboard_deadline_block": 900,
        "round_end_block": 1100,
    }
    scoreboard = {
        "round_id": state["round_id"],
        "validator_uid": 7,
        "stake_weight": 2.0,
        "rows": [{"entry_id": "entry", "score": 50.0}],
    }
    evaluations = []
    uploads = []
    polls = 0

    def evaluate(*args, **kwargs):
        assert kwargs == {"upload_scoreboard": False}
        evaluations.append(args)
        return scoreboard

    def upload(**payload):
        uploads.append(payload)
        if len(uploads) == 1:
            raise RuntimeError("504 Gateway Timeout")

    def sleep(_):
        nonlocal polls
        polls += 1
        if polls == 3:
            raise KeyboardInterrupt

    # The second poll is beyond the evaluation cutoff. Cached upload must still
    # retry there, unless the scoreboard deadline itself has passed.
    monkeypatch.setattr(loop.chain, "current_block", lambda: 200 if polls == 0 else (901 if expire else 700))
    monkeypatch.setattr(loop.chain, "close_subtensor", lambda: None)
    monkeypatch.setattr(loop, "run_round_evaluation", evaluate)
    monkeypatch.setattr(loop.time, "sleep", sleep)
    monkeypatch.setattr(loop.threading, "Thread", lambda **kwargs: SimpleNamespace(start=lambda: None, join=lambda **kw: None))
    api = SimpleNamespace(
        base_url="https://example.test",
        get_current_rounds=lambda: {"evaluating_round": state},
        upload_scoreboard=upload,
    )

    with pytest.raises(KeyboardInterrupt):
        loop.main_loop(object(), api)

    assert len(evaluations) == 1
    assert uploads == [scoreboard] * (1 if expire else 2)
