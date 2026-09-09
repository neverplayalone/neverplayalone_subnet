from __future__ import annotations

import pytest

from validator import round_evaluation


class _FakeAPI:
    """Fake APIClient recording slot/upload calls, optionally raising to
    simulate a storage failure that survived the HTTP-layer retries."""

    def __init__(self, *, fail_slot=False, fail_upload=False):
        self.fail_slot = fail_slot
        self.fail_upload = fail_upload
        self.uploads: list[str] = []
        self.slot_calls = 0

    def request_artifact_slot(self, *, artifact_kind, **kwargs):
        self.slot_calls += 1
        if self.fail_slot:
            raise RuntimeError("slot boom")
        return {
            "upload_url": f"https://storage.example/{artifact_kind}",
            "storage_key": f"key/{artifact_kind}",
        }

    def upload_bytes(self, upload_url, data):
        if self.fail_upload:
            raise RuntimeError("502 boom")
        self.uploads.append(upload_url)
        return {}


def _entry():
    return {
        "entry_kind": "submission",
        "miner_uid": 3,
        "miner_hotkey": "hk3",
    }


def _artifacts(tmp_path):
    report = tmp_path / "report.json"
    report.write_bytes(b"{}")
    recording = tmp_path / "recording.mcpr"
    recording.write_bytes(b"rec")
    return report, recording


def test_upload_entry_artifacts_returns_keys_on_success(tmp_path):
    api = _FakeAPI()
    report, recording = _artifacts(tmp_path)

    keys = round_evaluation._upload_entry_artifacts(
        api,
        round_id="2026-08-07",
        validator_uid=1,
        entry=_entry(),
        artifact_entry_id="e1__t0",
        report_path=report,
        recording_path=recording,
    )

    assert keys == {
        "report_s3_key": "key/report_json",
        "recording_s3_key": "key/recording_mcpr",
    }
    assert len(api.uploads) == 2


def test_upload_entry_artifacts_returns_none_on_upload_failure(tmp_path):
    api = _FakeAPI(fail_upload=True)
    report, recording = _artifacts(tmp_path)

    keys = round_evaluation._upload_entry_artifacts(
        api,
        round_id="2026-08-07",
        validator_uid=1,
        entry=_entry(),
        artifact_entry_id="e1__t0",
        report_path=report,
        recording_path=recording,
    )

    assert keys is None  # skip-and-continue, no exception propagated


def test_upload_entry_artifacts_returns_none_on_slot_failure(tmp_path):
    api = _FakeAPI(fail_slot=True)
    report, recording = _artifacts(tmp_path)

    keys = round_evaluation._upload_entry_artifacts(
        api,
        round_id="2026-08-07",
        validator_uid=1,
        entry=_entry(),
        artifact_entry_id="e1__t0",
        report_path=report,
        recording_path=recording,
    )

    assert keys is None
    assert api.uploads == []  # never reached the upload step
