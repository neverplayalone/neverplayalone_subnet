"""On-chain weight-row helpers the light validator uses to mirror the main validator."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from shared import chain


class FakeSubtensor:
    """Stands in for the chain: a fixed metagraph, stored weight rows, and a
    record of every weight submission."""

    def __init__(self, *, hotkeys, rows=(), owner_hotkey=None, accept=True):
        self.hotkeys = list(hotkeys)
        self.rows = list(rows)
        self.owner_hotkey = owner_hotkey
        self.accept = accept
        self.submitted: list[dict] = []

    def metagraph(self, netuid, block=None):
        del netuid, block
        return SimpleNamespace(hotkeys=self.hotkeys)

    def get_uid_for_hotkey_on_subnet(self, hotkey, netuid):
        del netuid
        return self.hotkeys.index(hotkey) if hotkey in self.hotkeys else None

    def weights(self, netuid):
        del netuid
        return self.rows

    def get_subnet_owner_hotkey(self, netuid):
        del netuid
        return self.owner_hotkey

    def set_weights(self, **kwargs):
        self.submitted.append(kwargs)
        return SimpleNamespace(success=self.accept, message="rejected" if not self.accept else "")


@pytest.fixture
def install(monkeypatch):
    def _install(fake: FakeSubtensor) -> FakeSubtensor:
        monkeypatch.setattr(chain, "get_subtensor", lambda: fake)
        return fake

    return _install


def _relative_weights(submission: dict) -> dict[int, float]:
    """Submitted weights as {uid: share of the largest weight}, zeros dropped."""
    pairs = {
        int(uid): float(weight)
        for uid, weight in zip(submission["uids"], submission["weights"])
        if float(weight) > 0
    }
    top = max(pairs.values())
    return {uid: round(weight / top, 3) for uid, weight in pairs.items()}


def test_weight_row_for_hotkey_returns_that_validators_nonzero_weights(install):
    install(
        FakeSubtensor(
            hotkeys=["main", "other", "m2", "m3"],
            rows=[(1, [(2, 65535)]), (0, [(3, 65535), (0, 6553), (2, 0)])],
        )
    )

    assert chain.weight_row_for_hotkey("main") == [(0, 6553), (3, 65535)]


def test_weight_row_for_hotkey_is_none_when_hotkey_is_not_registered(install):
    install(FakeSubtensor(hotkeys=["other"], rows=[(0, [(0, 65535)])]))

    assert chain.weight_row_for_hotkey("main") is None


def test_weight_row_for_hotkey_is_empty_when_validator_has_set_no_weights(install):
    install(FakeSubtensor(hotkeys=["main", "other"], rows=[(1, [(0, 65535)])]))

    assert chain.weight_row_for_hotkey("main") == []


def test_set_weight_row_submits_the_row_in_the_same_proportions(install):
    fake = install(FakeSubtensor(hotkeys=["a", "b", "c", "d"]))
    wallet = object()

    assert chain.set_weight_row(wallet, [(0, 32768), (3, 65535)]) is True

    assert len(fake.submitted) == 1
    assert fake.submitted[0]["wallet"] is wallet
    assert fake.submitted[0]["netuid"] == chain.NETUID
    assert _relative_weights(fake.submitted[0]) == {0: 0.5, 3: 1.0}


def test_set_weight_row_reports_failure_when_the_chain_rejects_the_submission(install):
    install(FakeSubtensor(hotkeys=["a", "b"], accept=False))

    assert chain.set_weight_row(object(), [(1, 65535)]) is False


def test_set_weight_row_ignores_uids_outside_the_current_metagraph(install):
    fake = install(FakeSubtensor(hotkeys=["a", "b"]))

    assert chain.set_weight_row(object(), [(1, 65535), (7, 65535)]) is True

    assert _relative_weights(fake.submitted[0]) == {1: 1.0}


def test_set_weight_row_submits_nothing_when_no_weight_is_left(install):
    fake = install(FakeSubtensor(hotkeys=["a", "b"]))

    assert chain.set_weight_row(object(), [(7, 65535)]) is False
    assert fake.submitted == []


def test_subnet_owner_hotkey_returns_the_owner_from_chain(install):
    install(FakeSubtensor(hotkeys=["a"], owner_hotkey="owner-hk"))

    assert chain.subnet_owner_hotkey() == "owner-hk"


def test_subnet_owner_hotkey_raises_when_chain_has_no_owner(install):
    install(FakeSubtensor(hotkeys=["a"], owner_hotkey=None))

    with pytest.raises(RuntimeError):
        chain.subnet_owner_hotkey()
