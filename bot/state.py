"""State persistence for the news bot.

Tracks:
- last_success_date: ISO date string of the last fully-delivered digest.
- pending_run:       Parts of an in-progress run (for resume-without-duplicate).
- last_error:        Short description of the last failure (for health command).
- last_error_time:   ISO datetime of last failure.

state.json is written atomically (write to .tmp, then rename) so a crash
mid-write never corrupts the file.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

STATE_PATH = Path(__file__).parent.parent / "state.json"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _load() -> dict:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Could not read state.json (%s); starting fresh.", exc)
    return {}


def _save(state: dict) -> None:
    """Write state atomically via a temp file."""
    tmp = STATE_PATH.with_suffix(".json.tmp")
    try:
        tmp.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")
        os.replace(tmp, STATE_PATH)
    except OSError as exc:
        logger.error("Failed to write state.json: %s", exc)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def is_already_sent(target_date: date) -> bool:
    """Return True if we already successfully delivered the digest for target_date."""
    state = _load()
    last = state.get("last_success_date")
    return last == target_date.isoformat()


def get_pending_parts(target_date: date, total_parts: int) -> list[int]:
    """Return part indices that have NOT yet been confirmed sent.

    On a fresh run: returns [0, 1, …, total_parts-1].
    On resume after a partial failure: returns the remaining indices.
    """
    state = _load()
    pending = state.get("pending_run", {})

    # If the pending run is for a different date, reset it
    if pending.get("date") != target_date.isoformat():
        return list(range(total_parts))

    if pending.get("total_parts") != total_parts:
        # Total changed (e.g. feeds changed) — start over for safety
        return list(range(total_parts))

    sent_set = set(pending.get("sent_parts", []))
    return [i for i in range(total_parts) if i not in sent_set]


def record_part_sent(
    target_date: date,
    part_idx: int,
    total_parts: int,
    message_id: int = 0,
    msg_id: int | None = None,
) -> None:
    """Record that one chunk was confirmed delivered by Telegram."""
    if msg_id is not None:
        message_id = msg_id
    state = _load()
    pending = state.setdefault("pending_run", {})

    if pending.get("date") != target_date.isoformat():
        # Fresh run
        pending.clear()
        pending["date"] = target_date.isoformat()
        pending["total_parts"] = total_parts
        pending["sent_parts"] = []
        pending["message_ids"] = {}

    if part_idx not in pending["sent_parts"]:
        pending["sent_parts"].append(part_idx)
    pending["message_ids"][str(part_idx)] = message_id

    _save(state)
    logger.debug("State: recorded part %d/%d sent (msg_id=%d)", part_idx + 1, total_parts, message_id)


def mark_success(target_date: date) -> None:
    """Mark the entire digest as successfully delivered."""
    state = _load()
    state["last_success_date"] = target_date.isoformat()
    state["last_error"] = None
    state["last_error_time"] = None
    # Keep pending_run for reference but clear it
    state.pop("pending_run", None)
    _save(state)
    logger.info("State: marked %s as fully delivered.", target_date)


def record_error(message: str) -> None:
    """Record an error in state for the health command."""
    state = _load()
    state["last_error"] = message[:500]
    state["last_error_time"] = datetime.now().isoformat(timespec="seconds")
    _save(state)


def get_health_info() -> dict:
    """Return a dict of health fields for the `health` command."""
    state = _load()
    return {
        "last_success_date": state.get("last_success_date", "never"),
        "last_poll_time":    state.get("last_poll_time", "never"),
        "posted_today_count": state.get("posted_today_count", 0),
        "seen_count":        len(state.get("seen_article_ids", [])),
        "last_error":        state.get("last_error"),
        "last_error_time":   state.get("last_error_time"),
        "pending_run":       state.get("pending_run"),
    }


def get_seen_ids() -> set[str]:
    """Return set of article IDs that have already been posted or seen."""
    state = _load()
    return set(state.get("seen_article_ids", []))


def mark_articles_seen(article_ids: list[str], max_history: int = 5000) -> None:
    """Store article IDs as seen to prevent re-posting."""
    state = _load()
    existing: list[str] = state.get("seen_article_ids", [])
    existing_set = set(existing)
    for aid in article_ids:
        if aid not in existing_set:
            existing.append(aid)
            existing_set.add(aid)
    if len(existing) > max_history:
        existing = existing[-max_history:]
    state["seen_article_ids"] = existing
    _save(state)


def get_seen_headlines() -> list[str]:
    """Return list of recent headlines that have been posted."""
    state = _load()
    return state.get("seen_headlines", [])


def record_posted_headline(headline: str, max_history: int = 1000) -> None:
    """Store posted headline for cross-poll deduplication."""
    if not headline:
        return
    state = _load()
    headlines: list[str] = state.get("seen_headlines", [])
    if headline not in headlines:
        headlines.append(headline)
    if len(headlines) > max_history:
        headlines = headlines[-max_history:]
    state["seen_headlines"] = headlines
    _save(state)


def record_poll_run(articles_posted: int = 0) -> None:
    """Record timestamp and stats for the latest poll run."""
    state = _load()
    state["last_poll_time"] = datetime.now().isoformat(timespec="seconds")
    today_str = date.today().isoformat()
    if state.get("today_date") != today_str:
        state["today_date"] = today_str
        state["posted_today_count"] = 0
    state["posted_today_count"] = state.get("posted_today_count", 0) + articles_posted
    _save(state)

