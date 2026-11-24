"""Validator entrypoint."""
from __future__ import annotations

import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv() -> None:
    env_file = _ROOT / ".env"
    if not env_file.exists():
        return
    for raw in env_file.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv()

import logging  # noqa: E402
from logging.handlers import QueueHandler, QueueListener  # noqa: E402
from queue import SimpleQueue  # noqa: E402

from shared import chain  # noqa: E402
from shared.api_client import APIClient  # noqa: E402
from validator.config import (  # noqa: E402
    API_URL,
    MAIN_VALIDATOR_HOTKEY,
    NETUID,
    NETWORK,
    PROXY_PORT,
    VALIDATOR_MODE,
)
from validator.light import light_loop  # noqa: E402
from validator.loop import main_loop  # noqa: E402
from validator.mode import LIGHT, select_mode  # noqa: E402


_LOG_QUEUE: SimpleQueue[logging.LogRecord] | None = None
_LOG_LISTENER: QueueListener | None = None


def _setup_logging() -> None:
    global _LOG_QUEUE, _LOG_LISTENER

    level = os.environ.get("NPA_LOG_LEVEL", "INFO").upper()
    level_value = getattr(logging, level, logging.INFO)

    if _LOG_LISTENER is None:
        _LOG_QUEUE = SimpleQueue()
        output_handler = logging.StreamHandler(sys.stdout)
        output_handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)s %(name)s: %(message)s",
                datefmt="%H:%M:%S",
            )
        )
        _LOG_LISTENER = QueueListener(_LOG_QUEUE, output_handler)
        _LOG_LISTENER.start()

    # Keep application logging asynchronous.  A blocked PM2 log pipe must not
    # prevent the batch coordinator from recording results or advancing to the
    # next evaluation.
    logging.basicConfig(
        level=level_value,
        handlers=[QueueHandler(_LOG_QUEUE)],
        force=True,
    )
    prefixes = ("npa", "validator", "shared", "npabench")
    for logger_name in ("npa", "npa.validator", "validator", "shared", "npabench"):
        logging.getLogger(logger_name).setLevel(level_value)
    for logger_name, logger_obj in logging.root.manager.loggerDict.items():
        if isinstance(logger_obj, logging.Logger) and logger_name.startswith(prefixes):
            logger_obj.setLevel(level_value)


def main() -> int:
    _setup_logging()
    log = logging.getLogger("npa.validator")

    wallet_name = os.environ.get("NPA_WALLET", "default")
    wallet_hotkey = os.environ.get("NPA_HOTKEY", "default")
    wallet = chain.make_wallet(wallet_name, wallet_hotkey)
    # bittensor mutates logging during wallet construction; restore our config
    # so validator progress logs remain visible afterwards.
    _setup_logging()

    log.info("hotkey=%s", wallet.hotkey.ss58_address)
    log.info(
        "wallet_name=%s wallet_hotkey=%s wallet_path=%s",
        wallet_name,
        wallet_hotkey,
        os.environ.get("NPA_BT_WALLET_DIR", ""),
    )
    log.info("netuid=%s network=%s", NETUID, NETWORK)

    try:
        mode, main_hotkey = select_mode(
            VALIDATOR_MODE,
            own_hotkey=wallet.hotkey.ss58_address,
            main_hotkey=MAIN_VALIDATOR_HOTKEY,
        )
    except Exception as exc:
        log.error("cannot select validator mode: %s", exc)
        return 1
    log.info("mode=%s", mode)

    if mode == LIGHT:
        try:
            light_loop(wallet, main_hotkey)
        except KeyboardInterrupt:
            log.info("interrupted")
        return 0

    if not VALIDATOR_MODE.strip():
        log.warning(
            "NPA_VALIDATOR_MODE is unset and this hotkey is the main validator; running full mode"
        )
    log.info("api=%s proxy_port=%s", API_URL, PROXY_PORT)

    api = APIClient(wallet, base_url=API_URL)
    try:
        api.health()
    except Exception as exc:
        log.error("backend unreachable at %s: %s", API_URL, exc)
        return 1

    try:
        main_loop(wallet, api)
    except KeyboardInterrupt:
        log.info("interrupted")
        return 0
    finally:
        api.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
