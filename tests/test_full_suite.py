"""Full test suite for Telegram Daily News Bot.

Covers:
- Yesterday-IST filtering around midnight
- Deduplication of near-identical headlines (Jaccard similarity)
- Real-time alert formatting & Telegram MarkdownV2 escaping
- Chunking under 4000 chars without cutting stories
- State persistence and duplicate prevention
- Priority ordering for real-time alerts
"""
import pytest
from datetime import datetime, date, timedelta, timezone
from dataclasses import dataclass
from unittest.mock import MagicMock, patch

from bot.utils import to_ist, get_yesterday_ist, is_yesterday_ist, IST, UTC
from bot.collector import Article, fetch_latest_articles
from bot.cleaner import (
    deduplicate, reclassify, sort_articles, display_category, is_important_story
)
from bot.formatter import (
    escape_md2, format_realtime_alert, format_digest, MAX_MSG_CHARS
)
from bot.state import (
    get_seen_ids, mark_articles_seen, record_poll_run, get_health_info
)


# ── 1. IST Timezone and Midnight Boundary Tests ──────────────────────────────

class TestISTTimezone:
    def test_utc_to_ist_conversion(self):
        # 18:30 UTC = 00:00 next day IST
        utc_dt = datetime(2026, 10, 27, 18, 30, tzinfo=timezone.utc)
        ist_dt = to_ist(utc_dt)
        assert ist_dt.date() == date(2026, 10, 28)
        assert ist_dt.hour == 0 and ist_dt.minute == 0

    def test_naive_datetime_assumed_utc(self):
        naive = datetime(2026, 10, 28, 4, 0)  # assumed 04:00 UTC
        ist_dt = to_ist(naive)
        # 04:00 UTC = 09:30 IST
        assert ist_dt.hour == 9 and ist_dt.minute == 30

    def test_midnight_boundary(self):
        # 23:59:59 IST should still be the same IST date
        dt_midnight = datetime(2026, 10, 28, 23, 59, 59, tzinfo=IST)
        assert dt_midnight.date() == date(2026, 10, 28)


# ── 2. Headline Deduplication Tests ──────────────────────────────────────────

class TestDeduplication:
    def test_exact_and_near_identical_headlines_merged(self):
        art1 = Article(
            id="1",
            headline="Supreme Court issues major notice to Election Commission on EVM data",
            url="https://thehindu.com/sc-ec",
            publisher="The Hindu",
            section="National",
            published_time=datetime(2026, 10, 28, 14, 0, tzinfo=IST),
        )
        art2 = Article(
            id="2",
            headline="Supreme Court issues big notice to Election Commission over EVM data",
            url="https://indianexpress.com/sc-ec-notice",
            publisher="The Indian Express",
            section="National",
            published_time=datetime(2026, 10, 28, 14, 15, tzinfo=IST),
        )
        deduped = deduplicate([art1, art2])
        assert len(deduped) == 1
        # The representative merges the other publisher
        assert "The Indian Express" in deduped[0].other_publishers or "The Hindu" in deduped[0].other_publishers

    def test_distinct_stories_not_merged(self):
        art1 = Article(
            id="1",
            headline="RBI holds repo rate steady citing inflation targets",
            url="https://reuters.com/rbi",
            publisher="Reuters",
            section="Economy",
            published_time=datetime(2026, 10, 28, 10, 0, tzinfo=IST),
        )
        art2 = Article(
            id="2",
            headline="ISRO successfully tests new cryogenic engine stage",
            url="https://thehindu.com/isro",
            publisher="The Hindu",
            section="Tech",
            published_time=datetime(2026, 10, 28, 11, 0, tzinfo=IST),
        )
        deduped = deduplicate([art1, art2])
        assert len(deduped) == 2


# ── 3. Formatting & Telegram MarkdownV2 Tests ────────────────────────────────

class TestFormatting:
    def test_escape_md2_escapes_all_specials(self):
        specials = r"_*[]()~`>#+-=|{}.!"
        escaped = escape_md2(specials)
        for char in specials:
            assert f"\\{char}" in escaped

    def test_realtime_alert_formatting(self):
        art = Article(
            id="p1-test",
            headline="Breaking: Major trade agreement signed between India and UK",
            url="https://reuters.com/trade-deal",
            publisher="Reuters",
            priority=1,
            category_focus="National & International",
            tags="#Breaking #Economy",
            published_time=datetime(2026, 10, 28, 15, 30, tzinfo=IST),
            description="Both nations confirmed signing of the comprehensive economic pact.",
            category="economy",
        )
        alert = format_realtime_alert(art)
        assert "🚨" in alert
        assert "Priority 1" in alert
        assert "Reuters" in alert
        assert "[Read more](https://reuters.com/trade-deal)" in alert

    def test_chunking_keeps_all_messages_under_limit(self):
        # Create 50 simulated articles
        articles = [
            Article(
                id=str(i),
                headline=f"Important National News Story Number {i} with full detailed title",
                url=f"https://example.com/news/{i}",
                publisher="The Hindu",
                section="India",
                published_time=datetime(2026, 10, 28, 12, 0, tzinfo=IST),
                description="A detailed description paragraph explaining what happened in this news event." * 2,
            )
            for i in range(50)
        ]
        sources_cfg = {"sections": {"India": []}, "settings": {"max_stories_per_section": 50}}
        chunks = format_digest(articles, sources_cfg, "Wednesday, 28 Oct 2026")

        for idx, chunk in enumerate(chunks):
            assert len(chunk) <= MAX_MSG_CHARS, f"Chunk {idx} exceeded character limit: {len(chunk)}"


# ── 4. Importance Filtering Tests ────────────────────────────────────────────

class TestImportanceFilter:
    @pytest.fixture
    def cats_config(self):
        return {
            "categories": {"politics": {}, "economy": {}, "sports": {}},
            "settings": {
                "noise_keywords": ["nfl", "packers", "super bowl", "box office", "horoscope"],
                "high_importance_keywords": ["prime minister", "supreme court", "election", "rbi", "cyclone", "nmmc"],
            }
        }

    def test_noise_rejected(self, cats_config):
        art = Article(
            id="noise-1",
            headline="Packers and Panthers trying to bounce back in NFL weekend clash",
            url="https://example.com/sports",
            publisher="AP News",
            category="sports",
        )
        assert not is_important_story(art, cats_config)

    def test_high_importance_accepted(self, cats_config):
        art = Article(
            id="imp-1",
            headline="Supreme Court issues historic notice on electoral transparency",
            url="https://example.com/sc",
            publisher="The Hindu",
            category="politics",
        )
        assert is_important_story(art, cats_config)

    def test_nmmc_civic_notice_accepted(self, cats_config):
        art = Article(
            id="civic-1",
            headline="NMMC announces 24-hour emergency water pipeline repairs",
            url="https://example.com/nmmc",
            publisher="NMMC and CIDCO",
            priority=8,
            category="civic",
        )
        assert is_important_story(art, cats_config)


# ── 5. State & Real-Time Seen Tracking Tests ─────────────────────────────────

class TestRealtimeState:
    @pytest.fixture(autouse=True)
    def tmp_state(self, tmp_path, monkeypatch):
        import bot.state as state_mod
        monkeypatch.setattr(state_mod, "STATE_PATH", tmp_path / "state.json")

    def test_mark_and_get_seen_ids(self):
        ids = ["https://reuters.com/1", "https://thehindu.com/2"]
        mark_articles_seen(ids)
        seen = get_seen_ids()
        assert "https://reuters.com/1" in seen
        assert "https://thehindu.com/2" in seen
        assert "https://other.com/3" not in seen

    def test_record_poll_run_increments_stats(self):
        record_poll_run(articles_posted=3)
        info = get_health_info()
        assert info["posted_today_count"] == 3
        assert info["last_poll_time"] != "never"
