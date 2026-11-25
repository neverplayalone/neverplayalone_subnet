"""Validator entrypoint."""
from __future__ import annotations

import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent


def _parse_dotenv(env_file: Path) -> dict[str, str]:
    """Read ``KEY=value`` lines the way the shell scripts do when they ``source``
    the file: the last assignment of a key wins and a leading ``export`` is allowed."""
    values: dict[str, str] = {}
    if not env_file.exists():
        return values
    for raw in env_file.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export ") :].strip()
        values[key] = value.strip().strip('"').strip("'")
    return values


def _load_dotenv(env_file: Path | None = None) -> list[str]:
    """Load ``.env`` into the process environment without overriding it.

    Returns the keys whose process-environment value differs from the file: for
    those an edit to ``.env`` is being ignored (pm2 keeps the environment a
    process was started with until it is restarted with ``--update-env``).
    """
    overridden = []
    for key, value in _parse_dotenv(env_file or _ROOT / ".env").items():
        if os.environ.setdefault(key, value) != value:
            overridden.append(key)
    return overridden


_DOTENV_OVERRIDDEN = _load_dotenv()

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


def _record_full_mode(env_file: Path) -> bool:
    """Write NPA_VALIDATOR_MODE=full to ``.env`` for the main validator.

    The setup and update scripts decide from ``.env`` whether to keep npabench
    current. The main validator runs full even with the mode unset, so without
    this line the scripts would treat it as light and let its npabench go stale.
    Returns True when the line was added.
    """
    try:
        if not env_file.exists():
            return False
        if _parse_dotenv(env_file).get("NPA_VALIDATOR_MODE", "").lower() in ("full", "heavy"):
            return False
        text = env_file.read_text()
        with env_file.open("a", encoding="utf-8") as handle:
            handle.write(
                ("" if not text or text.endswith("\n") else "\n")
                + "# Added by the validator: this hotkey is the main validator, which evaluates.\n"
                + "NPA_VALIDATOR_MODE=full\n"
            )
        return True
    except OSError:
        return False


def main() -> int:
    _setup_logging()
    log = logging.getLogger("npa.validator")
    if _DOTENV_OVERRIDDEN:
        log.warning(
            "the process environment overrides these .env settings: %s. If you edited .env, "
            "restart with `set -a; source .env; set +a; pm2 restart <name> --update-env`",
            ", ".join(sorted(_DOTENV_OVERRIDDEN)),
        )

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
        if _record_full_mode(_ROOT / ".env"):
            log.warning(
                "NPA_VALIDATOR_MODE is unset and this hotkey is the main validator: running full "
                "mode and recorded NPA_VALIDATOR_MODE=full in .env so the setup/update scripts "
                "keep npabench current. Restart the auto-updater so it picks this up."
            )
        else:
            log.warning(
                "NPA_VALIDATOR_MODE is unset and this hotkey is the main validator; running full "
                "mode. Set NPA_VALIDATOR_MODE=full in .env: the setup/update scripts only keep "
                "npabench current when the mode is set to full."
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
