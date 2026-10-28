"""Telegram API wrapper with retries, exponential backoff, and 429 handling.

Never logs or prints the bot token.
"""
from __future__ import annotations

import time
import logging
import requests as _requests

logger = logging.getLogger(__name__)

_BASE = "https://api.telegram.org/bot{token}/{method}"

# Retry settings
_MAX_RETRIES    = 5
_BASE_DELAY_S   = 2.0    # initial backoff
_MAX_DELAY_S    = 120.0  # cap per wait
_SEND_TIMEOUT_S = 30


class TelegramError(Exception):
    """Non-retryable Telegram API error."""


class TelegramRateLimit(Exception):
    """429 Too Many Requests; .retry_after seconds suggested."""
    def __init__(self, retry_after: float):
        self.retry_after = retry_after
        super().__init__(f"Rate limited; retry after {retry_after}s")


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------

def _url(token: str, method: str) -> str:
    return _BASE.format(token=token, method=method)


def _call(token: str, method: str, payload: dict, timeout: int = _SEND_TIMEOUT_S) -> dict:
    """POST to Telegram API. Raises TelegramRateLimit or TelegramError on failure."""
    url = _url(token, method)
    try:
        resp = _requests.post(url, json=payload, timeout=timeout)
    except _requests.RequestException as exc:
        raise ConnectionError(f"Network error calling {method}: {exc}") from exc

    if resp.status_code == 429:
        retry_after = float(
            resp.json().get("parameters", {}).get("retry_after", 30)
        )
        raise TelegramRateLimit(retry_after)

    data = resp.json()
    if not data.get("ok"):
        desc = data.get("description", "unknown error")
        raise TelegramError(f"Telegram API error ({resp.status_code}): {desc}")

    return data.get("result", {})


def _call_with_retry(token: str, method: str, payload: dict) -> dict:
    """Call Telegram with exponential backoff for network errors and 429s."""
    delay = _BASE_DELAY_S
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            return _call(token, method, payload)
        except TelegramRateLimit as exc:
            wait = min(exc.retry_after + 1, _MAX_DELAY_S)
            logger.warning(
                "Telegram rate-limited on attempt %d/%d; waiting %.0fs",
                attempt, _MAX_RETRIES, wait,
            )
            time.sleep(wait)
        except ConnectionError as exc:
            if attempt == _MAX_RETRIES:
                raise
            wait = min(delay, _MAX_DELAY_S)
            logger.warning(
                "Network error on attempt %d/%d (%s); retrying in %.0fs",
                attempt, _MAX_RETRIES, exc, wait,
            )
            time.sleep(wait)
            delay *= 2
        except TelegramError:
            raise  # non-retryable

    raise ConnectionError(f"All {_MAX_RETRIES} attempts failed for {method}")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_me(token: str) -> dict:
    """Validate token; return bot info dict."""
    return _call(token, "getMe", {})


def get_chat_administrators(token: str, chat_id: str) -> list[dict]:
    """Return list of administrator objects for the chat."""
    return _call(token, "getChatAdministrators", {"chat_id": chat_id})


def send_message(
    token: str,
    chat_id: str,
    text: str,
    parse_mode: str = "MarkdownV2",
    disable_web_page_preview: bool = True,
) -> int:
    """Send a message; return the Telegram message_id on success."""
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": disable_web_page_preview,
        "link_preview_options": {"is_disabled": disable_web_page_preview},
    }
    result = _call_with_retry(token, "sendMessage", payload)
    return result["message_id"]


def send_photo(
    token: str,
    chat_id: str,
    photo_url: str,
    caption: str = "",
    parse_mode: str = "MarkdownV2",
) -> int:
    """Send a photo with an optional MarkdownV2 caption.

    Telegram captions have a hard limit of 1024 characters.
    Returns the message_id on success.
    """
    if len(caption) > 1024:
        caption = caption[:1020].rsplit(" ", 1)[0] + "…"

    payload = {
        "chat_id": chat_id,
        "photo": photo_url,
        "caption": caption,
        "parse_mode": parse_mode,
    }
    result = _call_with_retry(token, "sendPhoto", payload)
    return result["message_id"]


def send_plain(token: str, chat_id: str, text: str) -> int:
    """Send a plain-text message (no parse_mode). Used for alert/error messages."""
    payload = {
        "chat_id": chat_id,
        "text": text[:4096],  # hard Telegram limit
    }
    result = _call_with_retry(token, "sendMessage", payload)
    return result["message_id"]
