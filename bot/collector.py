"""RSS feed collector: fetch, parse, and structure real-time and digest articles."""
from __future__ import annotations

import feedparser
import requests
import re
import logging
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Optional

from bot.utils import to_ist, get_ist_now

logger = logging.getLogger(__name__)


def _strip_html(text: str) -> str:
    """Remove HTML tags and decode common entities."""
    if not text:
        return ""
    gnews_match = re.match(r'^\s*<a\b[^>]*>(.+?)</a>', text, re.DOTALL)
    if gnews_match:
        text = gnews_match.group(1)

    text = re.sub(r"<[^>]+>", "", text)
    text = (
        text.replace("&amp;", "&")
            .replace("&lt;", "<")
            .replace("&gt;", ">")
            .replace("&quot;", '"')
            .replace("&#39;", "'")
            .replace("&#039;", "'")
            .replace("&nbsp;", " ")
    )
    return " ".join(text.split()).strip()


def _clean_description(desc: str, headline: str, publisher: str) -> str:
    """Remove boilerplate repetitions where summary equals headline."""
    stripped = desc.strip()
    if not stripped:
        return ""
    headline_norm = re.sub(r"\s+", " ", headline.strip().lower())
    desc_norm = re.sub(r"\s+", " ", stripped.lower())
    if desc_norm == headline_norm:
        return ""
    suffix = f" {publisher.lower()}"
    if desc_norm.endswith(suffix):
        candidate = desc_norm[: -len(suffix)].strip()
        if candidate == headline_norm:
            return ""
    return stripped


@dataclass
class Article:
    id: str
    headline: str
    url: str
    publisher: str
    priority: int = 5
    category_focus: str = "General"
    tags: str = ""
    published_time: datetime = field(default_factory=get_ist_now)
    section: str = "General"
    category: str = "Other"
    description: str = ""
    image_url: Optional[str] = None
    is_developing: bool = False
    other_publishers: list = field(default_factory=list)


def _extract_image_url(entry) -> Optional[str]:
    """Extract an image URL from an RSS entry if present."""
    # 1. media_content
    if hasattr(entry, "media_content") and entry.media_content:
        for m in entry.media_content:
            u = m.get("url")
            if u:
                return u

    # 2. media_thumbnail
    if hasattr(entry, "media_thumbnail") and entry.media_thumbnail:
        u = entry.media_thumbnail[0].get("url")
        if u:
            return u

    # 3. enclosures
    if hasattr(entry, "enclosures") and entry.enclosures:
        for enc in entry.enclosures:
            u = enc.get("url") or enc.get("href")
            mtype = enc.get("type", "")
            if u and (mtype.startswith("image/") or any(u.lower().endswith(ext) for ext in [".jpg", ".jpeg", ".png", ".webp"])):
                return u

    # 4. <img> tag in summary or content
    raw_html = ""
    if hasattr(entry, "summary"):
        raw_html += entry.summary
    if hasattr(entry, "content"):
        for c in entry.content:
            raw_html += c.get("value", "")

    if raw_html:
        m = re.search(r'<img[^>]+src=["\'](https?://[^"\'>]+)["\']', raw_html, re.IGNORECASE)
        if m:
            img_url = m.group(1)
            if not any(px in img_url.lower() for px in ["1x1", "pixel", "tracker", "feedsportal"]):
                return img_url

    return None



def _get_sources_list(sources_config: dict) -> list[dict]:
    """Helper to get a flat list of source dicts whether configured as list or sections."""
    if "sources" in sources_config:
        return sources_config["sources"]
    flat = []
    for section, sources in sources_config.get("sections", {}).items():
        for s in sources:
            item = dict(s)
            item.setdefault("category_focus", section)
            item.setdefault("priority", 5)
            flat.append(item)
    return flat


def fetch_articles(
    sources_config: dict,
    categories_config: dict,
    target_date: Optional[date] = None,
) -> list[Article]:
    """Fetch articles from sources for target_date (yesterday IST if None)."""
    from bot.utils import get_yesterday_ist
    if target_date is None:
        target_date = get_yesterday_ist()

    settings = sources_config.get("settings", {})
    timeout = settings.get("request_timeout", 10)
    user_agent = settings.get("user_agent", "Mozilla/5.0 TelegramNewsBot/2.0")
    headers = {"User-Agent": user_agent}

    matched: list[Article] = []
    sources = _get_sources_list(sources_config)

    for src in sources:
        name = src.get("name", "Unknown")
        url = src.get("url", "")
        priority = src.get("priority", 5)
        focus = src.get("category_focus", "General")
        tags = src.get("tag", "")

        try:
            resp = requests.get(url, headers=headers, timeout=timeout)
            resp.raise_for_status()
            feed = feedparser.parse(resp.content)
            for entry in feed.entries:
                art = _parse_entry_realtime(
                    entry,
                    source_name=name,
                    priority=priority,
                    category_focus=focus,
                    tags=tags,
                    categories_config=categories_config,
                )
                if art and art.published_time.date() == target_date:
                    matched.append(art)
        except Exception as exc:
            logger.warning("Error fetching feed '%s': %s", name, exc)

    return matched


def check_feeds(sources_config: dict) -> dict:
    """Test all configured feed URLs.

    Returns dict with 'working' and 'failed' lists.
    """
    settings = sources_config.get("settings", {})
    timeout = settings.get("request_timeout", 10)
    user_agent = settings.get("user_agent", "Mozilla/5.0 TelegramNewsBot/2.0")
    headers = {"User-Agent": user_agent}

    working = []
    failed = []

    sources = _get_sources_list(sources_config)
    for src in sources:
        name = src.get("name", "Unknown")
        url = src.get("url", "")
        priority = src.get("priority", 5)
        section = src.get("category_focus", "General")
        result = {
            "name": name,
            "url": url,
            "priority": priority,
            "section": section,
            "status": "OK",
            "entry_count": 0,
            "error": None,
        }
        try:
            resp = requests.get(url, headers=headers, timeout=timeout)
            resp.raise_for_status()
            feed = feedparser.parse(resp.content)
            count = len(feed.entries)
            result["entry_count"] = count
            working.append(result)
        except Exception as exc:
            result["status"] = "FAILED"
            result["error"] = str(exc)
            failed.append(result)

    return {"working": working, "failed": failed}


def fetch_latest_articles(
    sources_config: dict,
    categories_config: dict,
    seen_ids: Optional[set[str]] = None,
) -> list[Article]:
    """Fetch new articles from all priority sources that haven't been seen yet.

    Args:
        sources_config: loaded sources.yaml dict
        categories_config: loaded categories.yaml dict
        seen_ids: set of article IDs/URLs already processed

    Returns:
        List of new Article objects sorted by priority (1 is highest), then time.
    """
    if seen_ids is None:
        seen_ids = set()

    settings = sources_config.get("settings", {})
    timeout = settings.get("request_timeout", 10)
    user_agent = settings.get("user_agent", "Mozilla/5.0 TelegramNewsBot/2.0")
    max_age_minutes = settings.get("max_article_age_minutes", 30)
    headers = {"User-Agent": user_agent}

    now_ist = get_ist_now()
    new_articles: list[Article] = []
    discovered_urls: set[str] = set()

    sources = _get_sources_list(sources_config)
    # Sort sources by priority so higher priority sources take precedence
    sorted_sources = sorted(sources, key=lambda s: s.get("priority", 99))

    for src in sorted_sources:
        name = src.get("name", "Unknown")
        url = src.get("url", "")
        priority = src.get("priority", 5)
        focus = src.get("category_focus", "General")
        tags = src.get("tag", "")

        try:
            resp = requests.get(url, headers=headers, timeout=timeout)
            resp.raise_for_status()
            feed = feedparser.parse(resp.content)

            for entry in feed.entries:
                art = _parse_entry_realtime(
                    entry,
                    source_name=name,
                    priority=priority,
                    category_focus=focus,
                    tags=tags,
                    categories_config=categories_config,
                )
                if not art:
                    continue

                # Only accept articles published recently (within max_age_minutes)
                if max_age_minutes is not None:
                    age_mins = (now_ist - art.published_time).total_seconds() / 60
                    if age_mins > max_age_minutes:
                        continue

                if art.id in seen_ids or art.url in seen_ids:
                    continue

                if art.url in discovered_urls:
                    continue

                discovered_urls.add(art.url)
                new_articles.append(art)

        except Exception as exc:
            logger.warning("Error fetching feed '%s': %s", name, exc)

    # Sort: Priority 1 first, then latest published time
    new_articles.sort(key=lambda a: (a.priority, -a.published_time.timestamp()))
    return new_articles


def _parse_entry_realtime(
    entry,
    source_name: str,
    priority: int,
    category_focus: str,
    tags: str,
    categories_config: dict,
) -> Optional[Article]:
    """Parse a single feed entry into a real-time Article."""
    headline = _strip_html(getattr(entry, "title", "") or "").strip()
    if not headline:
        return None

    url = (getattr(entry, "link", "") or "").strip()
    entry_id = getattr(entry, "id", "") or url
    if not url and not entry_id:
        return None

    # Determine publisher name (e.g. from Google News source tag if present)
    actual_publisher = source_name
    source_tag = getattr(entry, "source", None)
    if source_tag:
        stitle = getattr(source_tag, "title", None)
        if stitle:
            actual_publisher = stitle.strip()

    # Time parsing
    pub_parsed = getattr(entry, "published_parsed", None) or getattr(entry, "updated_parsed", None)
    if pub_parsed:
        try:
            pub_utc = datetime(*pub_parsed[:6], tzinfo=timezone.utc)
            pub_ist = to_ist(pub_utc)
        except Exception:
            pub_ist = get_ist_now()
    else:
        pub_ist = get_ist_now()

    # Description
    raw_desc = (getattr(entry, "summary", "") or "").strip()
    desc = _strip_html(raw_desc)
    desc = _clean_description(desc, headline, actual_publisher)

    # Category and developing status
    combined = f"{headline} {desc}"
    category = _classify_category(combined, categories_config)
    is_developing = _is_developing(combined, categories_config)

    # Image extraction
    img_url = _extract_image_url(entry)

    return Article(
        id=entry_id,
        headline=headline,
        url=url,
        publisher=actual_publisher,
        priority=priority,
        category_focus=category_focus,
        tags=tags,
        published_time=pub_ist,
        section=category_focus,
        category=category,
        description=desc,
        image_url=img_url,
        is_developing=is_developing,
    )


def _classify_category(text: str, categories_config: dict) -> str:
    text_lower = text.lower()
    best_cat = "Other"
    best_count = 0
    for cat_name, cat_data in categories_config.get("categories", {}).items():
        kws = cat_data.get("keywords", [])
        cnt = sum(1 for kw in kws if kw.lower() in text_lower)
        if cnt > best_count:
            best_count = cnt
            best_cat = cat_name
    return best_cat


def _is_developing(text: str, categories_config: dict) -> bool:
    text_lower = text.lower()
    dev_kws = categories_config.get("settings", {}).get("developing_keywords", [])
    return any(kw.lower() in text_lower for kw in dev_kws)
