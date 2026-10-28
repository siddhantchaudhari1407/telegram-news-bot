"""Phase 4 unit tests: state machine, sender retry logic (mocked Telegram)."""
import pytest
import json
import time
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch, MagicMock, call


# ── State machine tests ──────────────────────────────────────────────────────

class TestState:
    """Tests for bot/state.py using a temporary state file."""

    @pytest.fixture(autouse=True)
    def tmp_state(self, tmp_path, monkeypatch):
        """Redirect STATE_PATH to a temp file for each test."""
        import bot.state as state_mod
        monkeypatch.setattr(state_mod, "STATE_PATH", tmp_path / "state.json")
        yield tmp_path / "state.json"

    def test_not_sent_initially(self):
        from bot.state import is_already_sent
        assert not is_already_sent(date(2026, 10, 28))

    def test_mark_success_prevents_resend(self):
        from bot.state import mark_success, is_already_sent
        d = date(2026, 10, 28)
        mark_success(d)
        assert is_already_sent(d)

    def test_different_date_not_blocked(self):
        from bot.state import mark_success, is_already_sent
        mark_success(date(2026, 10, 27))
        assert not is_already_sent(date(2026, 10, 28))

    def test_get_pending_parts_fresh_run(self):
        from bot.state import get_pending_parts
        assert get_pending_parts(date(2026, 10, 28), 5) == [0, 1, 2, 3, 4]

    def test_record_part_sent_reduces_pending(self):
        from bot.state import get_pending_parts, record_part_sent
        d = date(2026, 10, 28)
        total = 3
        record_part_sent(d, 0, total, msg_id=100)
        record_part_sent(d, 2, total, msg_id=102)
        pending = get_pending_parts(d, total)
        assert pending == [1]

    def test_record_error_stored_in_health(self):
        from bot.state import record_error, get_health_info
        record_error("Something went wrong")
        info = get_health_info()
        assert info["last_error"] == "Something went wrong"
        assert info["last_error_time"] is not None

    def test_mark_success_clears_error(self):
        from bot.state import record_error, mark_success, get_health_info
        record_error("bad thing")
        mark_success(date(2026, 10, 28))
        info = get_health_info()
        assert info["last_error"] is None

    def test_pending_run_different_date_resets(self):
        """If state has a pending run for a different date, get_pending_parts resets."""
        from bot.state import record_part_sent, get_pending_parts
        record_part_sent(date(2026, 10, 27), 0, 5, msg_id=1)
        # Now ask for a different date — should get all parts
        pending = get_pending_parts(date(2026, 10, 28), 5)
        assert pending == [0, 1, 2, 3, 4]

    def test_atomic_write_no_corruption(self, tmp_state):
        """State file should be valid JSON after write."""
        from bot.state import mark_success
        mark_success(date(2026, 10, 28))
        data = json.loads(tmp_state.read_text())
        assert data["last_success_date"] == "2026-10-28"


# ── Sender retry logic tests ─────────────────────────────────────────────────

class TestSenderRetry:
    """Tests for bot/sender.py retry and backoff logic (no real network calls)."""

    def _make_response(self, status_code: int, body: dict):
        resp = MagicMock()
        resp.status_code = status_code
        resp.json.return_value = body
        return resp

    def test_send_message_success(self):
        from bot.sender import send_message
        ok_resp = self._make_response(200, {"ok": True, "result": {"message_id": 42}})
        with patch("bot.sender._requests.post", return_value=ok_resp) as mock_post:
            msg_id = send_message("TOKEN", "@chan", "hello")
        assert msg_id == 42
        assert mock_post.call_count == 1

    def test_send_message_retries_on_network_error(self):
        from bot.sender import send_message
        import requests as req_lib
        ok_resp = self._make_response(200, {"ok": True, "result": {"message_id": 7}})
        with patch("bot.sender._requests.post",
                   side_effect=[req_lib.ConnectionError("timeout"), ok_resp]) as mock_post, \
             patch("bot.sender.time.sleep"):
            msg_id = send_message("TOKEN", "@chan", "hello")
        assert msg_id == 7
        assert mock_post.call_count == 2

    def test_send_message_respects_429_retry_after(self):
        from bot.sender import send_message
        rate_resp = self._make_response(429, {"ok": False, "parameters": {"retry_after": 5}})
        ok_resp   = self._make_response(200, {"ok": True, "result": {"message_id": 9}})
        sleeps = []
        with patch("bot.sender._requests.post", side_effect=[rate_resp, ok_resp]), \
             patch("bot.sender.time.sleep", side_effect=lambda s: sleeps.append(s)):
            msg_id = send_message("TOKEN", "@chan", "hello")
        assert msg_id == 9
        # Should have slept for retry_after + 1
        assert any(s >= 5 for s in sleeps)

    def test_send_message_raises_telegram_error_on_bad_token(self):
        from bot.sender import send_message, TelegramError
        bad_resp = self._make_response(401, {"ok": False, "description": "Unauthorized"})
        with patch("bot.sender._requests.post", return_value=bad_resp):
            with pytest.raises(TelegramError, match="Unauthorized"):
                send_message("BADTOKEN", "@chan", "hello")

    def test_send_message_raises_after_max_retries(self):
        from bot.sender import send_message
        import requests as req_lib
        with patch("bot.sender._requests.post",
                   side_effect=req_lib.ConnectionError("always fails")), \
             patch("bot.sender.time.sleep"):
            with pytest.raises(ConnectionError):
                send_message("TOKEN", "@chan", "hello")

    def test_get_me_returns_bot_info(self):
        from bot.sender import get_me
        ok_resp = self._make_response(200, {"ok": True, "result": {"id": 1, "username": "testbot"}})
        with patch("bot.sender._requests.post", return_value=ok_resp):
            info = get_me("TOKEN")
        assert info["username"] == "testbot"


# ── Runner duplicate-guard test ───────────────────────────────────────────────

class TestRunnerDuplicateGuard:
    """run_digest must not send when the date is already marked as sent."""

    @pytest.fixture(autouse=True)
    def tmp_state(self, tmp_path, monkeypatch):
        import bot.state as state_mod
        monkeypatch.setattr(state_mod, "STATE_PATH", tmp_path / "state.json")

    def test_skips_if_already_sent(self, monkeypatch):
        """If state says already sent, run_digest returns True without calling send_message."""
        from bot.state import mark_success
        from bot.utils import get_yesterday_ist
        mark_success(get_yesterday_ist())

        send_calls = []
        monkeypatch.setattr("bot.sender.send_message", lambda *a, **k: send_calls.append(1) or 99)

        from bot.runner import run_digest
        result = run_digest("TOKEN", "@chan", {"sections": {}, "settings": {}}, {"categories": {}, "settings": {}})
        assert result is True
        assert send_calls == [], "send_message should not have been called"


class TestNationalStateFilter:
    """Verifies that international news is rejected and national/state news is accepted."""

    def test_rejects_international_stories(self):
        from bot.cleaner import is_important_story
        from bot.collector import Article
        from bot.config import load_config

        cats = load_config()["categories"]

        intl_articles = [
            Article(id="1", headline="Donald Trump speaks at rally in Pennsylvania", description="US election updates", url="http://x", publisher="Reuters", section="world", priority=1),
            Article(id="2", headline="Gaza ceasefire talks hit deadlock in Cairo", description="Middle East conflict", url="http://x", publisher="Reuters", section="world", priority=1),
            Article(id="3", headline="White House releases statement on European Union trade", description="US trade", url="http://x", publisher="Reuters", section="world", priority=1),
        ]

        for art in intl_articles:
            assert not is_important_story(art, cats), f"Expected '{art.headline}' to be rejected as international news"

    def test_accepts_national_and_state_stories(self):
        from bot.cleaner import is_important_story
        from bot.collector import Article
        from bot.config import load_config

        cats = load_config()["categories"]

        nat_articles = [
            Article(id="4", headline="Supreme Court issues notice on national reservation policy", description="Cabinet decisions", url="http://x", publisher="The Hindu", section="national", priority=2),
            Article(id="5", headline="NMMC announces 24-hour water cut across Navi Mumbai due to Morbe dam repairs", description="Civic water supply", url="http://x", publisher="NMMC", section="civic", priority=7),
            Article(id="6", headline="Central Railway announces special suburban local trains for Mumbai commuters", description="Local train infrastructure", url="http://x", publisher="Hindustan Times", section="mumbai", priority=4),
        ]

        for art in nat_articles:
            assert is_important_story(art, cats), f"Expected '{art.headline}' to be accepted as national/state news"



class TestSentenceCompletion:
    """Verifies that truncated descriptions are completed into grammatically closed sentences."""

    def test_drops_truncated_trailing_clause(self):
        from bot.formatter import clean_and_complete_description
        raw = "The Supreme Court on Wednesday stayed the High Court order on elections. A bench led by CJI issued notice. The matter will be heard next week because..."
        cleaned = clean_and_complete_description(raw)
        assert cleaned == "The Supreme Court on Wednesday stayed the High Court order on elections. A bench led by CJI issued notice."
        assert cleaned.endswith(".")
        assert "because" not in cleaned

    def test_completes_unpunctuated_single_sentence(self):
        from bot.formatter import clean_and_complete_description
        raw = "Central Railway announces mega block on harbour line between CSMT and Vadala Road for maintenance and"
        cleaned = clean_and_complete_description(raw)
        assert cleaned == "Central Railway announces mega block on harbour line between CSMT and Vadala Road for maintenance."
        assert cleaned.endswith(".")
        assert not cleaned.endswith("and.")


class TestCrossSourceDeduplication:
    """Verifies duplicate news stories across different publishers and runs are caught."""

    def test_cross_source_headline_matching(self):
        from bot.cleaner import are_headlines_duplicate
        h1 = "3 killed, 20 injured after express train derails near Jalgaon in Maharashtra"
        h2 = "Maharashtra: 3 dead as train derails near Jalgaon; rescue operations underway"
        assert are_headlines_duplicate(h1, h2) is True

    def test_persistent_seen_headlines(self, tmp_path, monkeypatch):
        import bot.state as state_mod
        from bot.cleaner import are_headlines_duplicate
        monkeypatch.setattr(state_mod, "STATE_PATH", tmp_path / "state.json")

        state_mod.record_posted_headline("3 killed, 20 injured after express train derails near Jalgaon in Maharashtra")
        seen = state_mod.get_seen_headlines()
        assert len(seen) == 1

        new_incoming = "Maharashtra: 3 dead as train derails near Jalgaon; rescue operations underway"
        is_dup = any(are_headlines_duplicate(new_incoming, prev) for prev in seen)
        assert is_dup is True
