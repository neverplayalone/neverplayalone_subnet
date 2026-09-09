from __future__ import annotations

import importlib.util
import json
import sys
import types
import unittest
from unittest import mock

if importlib.util.find_spec("httpx") is None:
    sys.modules["httpx"] = types.SimpleNamespace(Client=lambda timeout=None: object())

import httpx

from shared import api_client
from shared.api_client import APIClient

# The retry tests build real httpx exception instances; skip them when httpx is
# only the SimpleNamespace shim installed above (no exception classes).
_HTTPX_REAL = hasattr(httpx, "HTTPStatusError")


def _http_status_error(status: int) -> "httpx.HTTPStatusError":
    request = httpx.Request("PUT", "https://storage.example/upload")
    response = httpx.Response(status, request=request)
    return httpx.HTTPStatusError(f"HTTP {status}", request=request, response=response)


class _FlakyPutClient:
    """Fake HTTP client whose put() raises the queued exceptions on successive
    calls, then returns a successful empty response once they are exhausted."""

    def __init__(self, failures):
        self._failures = list(failures)
        self.put_calls = 0

    def put(self, url, content=None, headers=None):
        self.put_calls += 1
        if self._failures:
            raise self._failures.pop(0)
        return _FakeResponse(content=b"", headers={})


class _FakeHotkey:
    ss58_address = "hotkey-1"

    def sign(self, payload: bytes) -> bytes:
        return b"signed:" + payload


class _FakeWallet:
    hotkey = _FakeHotkey()


class _FakeResponse:
    def __init__(self, payload=None, *, content: bytes = b"", headers: dict[str, str] | None = None):
        self._payload = payload
        self.content = content
        self.headers = headers or {}

    def raise_for_status(self) -> None:
        return None

    def json(self):
        if self._payload is not None:
            return self._payload
        return json.loads(self.content)


class _RecordingHTTPClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, str] | None, bytes | None]] = []

    def get(self, url: str, headers: dict[str, str] | None = None):
        self.calls.append(("GET", url, headers, None))
        return _FakeResponse({"ok": True})

    def post(self, url: str, content: bytes | None = None, headers: dict[str, str] | None = None):
        self.calls.append(("POST", url, headers, content))
        return _FakeResponse({"ok": True})

    def put(self, url: str, content: bytes | None = None, headers: dict[str, str] | None = None):
        self.calls.append(("PUT", url, headers, content))
        return _FakeResponse(content=b"", headers={})

    def close(self) -> None:
        return None


class APIClientTests(unittest.TestCase):
    def test_validator_roster_get_is_signed(self) -> None:
        client = APIClient(_FakeWallet(), base_url="http://127.0.0.1:8000")
        http_client = _RecordingHTTPClient()
        client._client = http_client

        client.get_round_roster(7)

        method, url, headers, body = http_client.calls[0]
        self.assertEqual(method, "GET")
        self.assertEqual(url, "http://127.0.0.1:8000/validator/rounds/7/roster")
        self.assertIsNone(body)
        self.assertIsNotNone(headers)
        assert headers is not None
        self.assertEqual(headers["X-Hotkey"], "hotkey-1")
        self.assertIn("X-Nonce", headers)
        self.assertIn("X-Timestamp", headers)
        self.assertIn("X-Signature", headers)

    def test_public_validator_current_round_get_remains_unsigned(self) -> None:
        client = APIClient(_FakeWallet(), base_url="http://127.0.0.1:8000")
        http_client = _RecordingHTTPClient()
        client._client = http_client

        client.get_current_rounds()

        method, url, headers, body = http_client.calls[0]
        self.assertEqual(method, "GET")
        self.assertEqual(url, "http://127.0.0.1:8000/validator/rounds/current")
        self.assertIsNone(body)
        self.assertIsNone(headers)

    def test_hotkey_eligibility_is_signed(self) -> None:
        client = APIClient(_FakeWallet(), base_url="http://127.0.0.1:8000")
        http_client = _RecordingHTTPClient()
        client._client = http_client

        client.hotkey_eligibility(["miner-a", "miner-b"])

        method, url, headers, body = http_client.calls[0]
        self.assertEqual(method, "POST")
        self.assertEqual(url, "http://127.0.0.1:8000/validator/hotkeys/eligibility")
        self.assertEqual(json.loads(body), {"hotkeys": ["miner-a", "miner-b"]})
        assert headers is not None
        self.assertEqual(headers["X-Hotkey"], "hotkey-1")

    def test_upload_put_accepts_empty_non_json_response(self) -> None:
        client = APIClient(_FakeWallet(), base_url="http://127.0.0.1:8000")
        http_client = _RecordingHTTPClient()
        client._client = http_client

        result = client.upload_bytes("https://storage.example/upload", b"abc")

        method, url, headers, body = http_client.calls[0]
        self.assertEqual(method, "PUT")
        self.assertEqual(url, "https://storage.example/upload")
        self.assertEqual(body, b"abc")
        self.assertIsNone(headers)
        self.assertEqual(result, {})


@unittest.skipUnless(_HTTPX_REAL, "requires real httpx exception classes")
class UploadRetryTests(unittest.TestCase):
    def setUp(self) -> None:
        # Never actually sleep during retry tests, and pin the attempt budget so
        # the assertions do not depend on the NPA_UPLOAD_MAX_ATTEMPTS env var.
        sleep_patch = mock.patch.object(api_client.time, "sleep")
        attempts_patch = mock.patch.object(api_client, "UPLOAD_MAX_ATTEMPTS", 4)
        self.sleep = sleep_patch.start()
        attempts_patch.start()
        self.addCleanup(sleep_patch.stop)
        self.addCleanup(attempts_patch.stop)

    def _client_with(self, failures):
        client = APIClient(_FakeWallet(), base_url="http://127.0.0.1:8000")
        http_client = _FlakyPutClient(failures)
        client._client = http_client
        return client, http_client

    def test_retries_transient_502_then_succeeds(self) -> None:
        client, http_client = self._client_with(
            [_http_status_error(502), _http_status_error(502)]
        )

        result = client.upload_bytes("https://storage.example/upload", b"abc")

        self.assertEqual(result, {})
        self.assertEqual(http_client.put_calls, 3)
        self.assertEqual(self.sleep.call_count, 2)

    def test_retries_connection_error_then_succeeds(self) -> None:
        client, http_client = self._client_with([httpx.ConnectError("boom")])

        result = client.upload_bytes("https://storage.example/upload", b"abc")

        self.assertEqual(result, {})
        self.assertEqual(http_client.put_calls, 2)

    def test_gives_up_after_max_attempts(self) -> None:
        client, http_client = self._client_with([_http_status_error(502)] * 5)

        with self.assertRaises(httpx.HTTPStatusError):
            client.upload_bytes("https://storage.example/upload", b"abc")

        self.assertEqual(http_client.put_calls, 4)  # UPLOAD_MAX_ATTEMPTS

    def test_does_not_retry_client_error(self) -> None:
        client, http_client = self._client_with([_http_status_error(403)])

        with self.assertRaises(httpx.HTTPStatusError):
            client.upload_bytes("https://storage.example/upload", b"abc")

        self.assertEqual(http_client.put_calls, 1)  # no retry on 4xx
        self.sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
