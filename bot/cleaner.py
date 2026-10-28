"""Phase 2 – Deduplication and classification refinements.

Headline deduplication uses token-overlap similarity, abbreviation expansion,
and morphological stemming (Jaccard + Overlap Coefficient).
Supports global cross-source deduplication to ensure different newsrooms
reporting on the same breaking story do not cause duplicate Telegram alerts.

All classification is keyword-based from categories.yaml.
"""
from __future__ import annotations

import re
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bot.collector import Article

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

SIMILARITY_THRESHOLD = 0.40   # Jaccard similarity threshold for clustering


def deduplicate(articles: list["Article"], by_section: bool = False) -> list["Article"]:
    """Remove near-identical headlines across sources and sections.

    Args:
        articles:   List of candidate Article objects.
        by_section: If True, dedup only within section. If False (default),
                    dedup globally across all feeds so different newsrooms
                    (e.g. Reuters vs The Hindu vs Indian Express) do not post
                    duplicates of the same breaking news event.
    """
    if not articles:
        return []

    if by_section:
        from collections import defaultdict
        by_sec: dict[str, list["Article"]] = defaultdict(list)
        for art in articles:
            by_sec[art.section].append(art)

        result: list["Article"] = []
        for section, arts in by_sec.items():
            result.extend(_dedup_cluster(arts))
        return result
    else:
        return _dedup_cluster(articles)


def reclassify(articles: list["Article"], categories_config: dict) -> list["Article"]:
    """Re-run classification on every article (idempotent)."""
    for art in articles:
        combined = f"{art.headline} {art.description}"
        art.category = _classify(combined, categories_config)
        art.is_developing = _is_developing(combined, categories_config)
    return articles


def sort_articles(articles: list["Article"]) -> list["Article"]:
    """Sort articles by publisher count desc, then priority asc, then time desc."""
    return sorted(
        articles,
        key=lambda a: (
            a.section,
            -(len(a.other_publishers) + 1),
            getattr(a, "priority", 5),
            -a.published_time.timestamp(),
        ),
        reverse=False,
    )


# ---------------------------------------------------------------------------
# Internal helpers & tokenisation
# ---------------------------------------------------------------------------

_STOPWORDS = frozenset(
    "a an the and or but in on at to of for is are was were be been "
    "being have has had do does did will would could should may might "
    "shall can s its it this that with from by as into after before "
    "over under through between against during without says said amid over".split()
)

_ABBREV = {
    "sc": "supreme court",
    "hc": "high court",
    "cji": "chief justice",
    "pm": "prime minister",
    "cm": "chief minister",
    "bjp": "bharatiya janata party",
    "cr": "central railway",
    "wr": "western railway",
    "pts": "point",
    "points": "point",
    "dead": "kill",
    "dies": "kill",
    "died": "kill",
    "fatal": "kill",
    "plunges": "fall",
    "crashes": "fall",
    "tumbles": "fall",
    "ruling": "order",
    "verdict": "order",
}


def _stem(word: str) -> str:
    """Lightweight suffix stemmer for news headlines."""
    w = word.lower()
    for suffix in ("ing", "ment", "tion", "sion", "ness", "able", "ible", "ies", "es", "ed", "s"):
        if w.endswith(suffix) and len(w) - len(suffix) >= 3:
            return w[:-len(suffix)]
    return w


def _tokenise(text: str) -> frozenset[str]:
    """Lower-case, expand abbreviations, stem, and return meaningful token set."""
    clean = re.sub(r"[^a-z0-9\s]", " ", text.lower())
    words = clean.split()
    expanded = []
    for w in words:
        if w in _ABBREV:
            expanded.extend(_ABBREV[w].split())
        else:
            expanded.append(w)
    return frozenset(_stem(w) for w in expanded if w not in _STOPWORDS and len(w) > 1)


def are_headlines_duplicate(h1: str, h2: str) -> bool:
    """Return True if two headlines refer to the same news event."""
    t1 = _tokenise(h1)
    t2 = _tokenise(h2)
    if not t1 or not t2:
        return False

    inter = len(t1 & t2)
    union = len(t1 | t2)
    jaccard = inter / union if union else 0.0
    min_len = min(len(t1), len(t2))
    overlap = inter / min_len if min_len else 0.0

    # Match if Jaccard similarity is high OR strong subset overlap on at least 3 key words
    return jaccard >= SIMILARITY_THRESHOLD or (inter >= 3 and overlap >= 0.48)


def _dedup_cluster(arts: list["Article"]) -> list["Article"]:
    """Cluster near-identical headlines across candidates; keep one representative per cluster."""
    if not arts:
        return []

    n = len(arts)
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x: int, y: int) -> None:
        px, py = find(x), find(y)
        if px != py:
            parent[px] = py

    for i in range(n):
        for j in range(i + 1, n):
            if are_headlines_duplicate(arts[i].headline, arts[j].headline):
                union(i, j)

    # Build clusters
    clusters: dict[int, list[int]] = {}
    for i in range(n):
        root = find(i)
        clusters.setdefault(root, []).append(i)

    deduped: list["Article"] = []
    for indices in clusters.values():
        if len(indices) == 1:
            deduped.append(arts[indices[0]])
            continue

        cluster_arts = [arts[i] for i in indices]
        # Prefer higher priority (lower number, e.g. P1 Reuters > P3 IE), then most publishers, then earliest time
        representative = min(
            cluster_arts,
            key=lambda a: (
                getattr(a, "priority", 5),
                -len(a.other_publishers),
                a.published_time.timestamp(),
            ),
        )

        # Merge all publishers
        all_publishers: set[str] = set(representative.other_publishers)
        all_publishers.add(representative.publisher)
        for art in cluster_arts:
            if art is representative:
                continue
            all_publishers.add(art.publisher)
            all_publishers.update(art.other_publishers)
            if len(art.description) > len(representative.description):
                representative.description = art.description
            if not representative.image_url and art.image_url:
                representative.image_url = art.image_url

        all_publishers.discard(representative.publisher)
        representative.other_publishers = sorted(all_publishers)

        merged_count = len(indices)
        if merged_count > 1:
            logger.info(
                "Dedup merged %d articles → '%s' (sources: %s, %s)",
                merged_count,
                representative.headline[:60],
                representative.publisher,
                ", ".join(representative.other_publishers) or "none",
            )

        deduped.append(representative)

    return deduped


# Map internal YAML keys → human-friendly display names
_CATEGORY_DISPLAY = {
    "politics":       "Politics",
    "economy":        "Economy",
    "business":       "Business",
    "tech_ai":        "Tech / AI",
    "education":      "Education",
    "jobs":           "Jobs",
    "crime":          "Crime",
    "accidents":      "Accidents",
    "weather":        "Weather",
    "safety":         "Safety",
    "sports":         "Sports",
    "civic":          "Civic",
    "trains":         "Trains",
    "traffic":        "Traffic",
    "infrastructure": "Infrastructure",
    "water":          "Water",
    "Other":          "Other",
}


def display_category(cat_key: str) -> str:
    """Return human-readable category label."""
    return _CATEGORY_DISPLAY.get(cat_key, cat_key.replace("_", " ").title())


def _classify(text: str, categories_config: dict) -> str:
    """Keyword-based classification; returns category key with most hits."""
    text_lower = text.lower()
    best_cat = "Other"
    best_count = 0

    for cat_name, cat_data in categories_config.get("categories", {}).items():
        keywords = cat_data.get("keywords", [])
        count = sum(1 for kw in keywords if kw.lower() in text_lower)
        if count > best_count:
            best_count = count
            best_cat = cat_name

    return best_cat


def _is_developing(text: str, categories_config: dict) -> bool:
    """Return True if text signals a developing / unverified story."""
    text_lower = text.lower()
    dev_kws = categories_config.get("settings", {}).get("developing_keywords", [])
    return any(kw.lower() in text_lower for kw in dev_kws)


def is_important_story(art: "Article", categories_config: dict) -> bool:
    """Filter out noise, routine match scores, international news, and low-impact fluff.

    Criteria for important news (National & State news only):
    1. Reject if headline matches noise_keywords (sports fluff, entertainment, foreign/international affairs).
    2. Accept if matches high_importance_keywords (e.g. PM, cabinet, elections, SC, RBI, disaster).
    3. Accept official civic & infrastructure notices (NMMC, CIDCO, Navi Mumbai infrastructure).
    4. Accept core hard-news categories (politics, economy, infrastructure, civic, safety, accidents, trains, water).
    5. Filter out routine sports match scores, entertainment, and non-essential filler.
    """
    settings = categories_config.get("settings", {})
    noise_kws = settings.get("noise_keywords", [])
    high_kws = settings.get("high_importance_keywords", [])

    text_lower = f"{art.headline} {art.description}".lower()

    # 1. Noise check (sports fluff, entertainment gossip, foreign affairs)
    for kw in noise_kws:
        if kw.lower() in text_lower:
            return False

    # 2. High importance keywords override
    for kw in high_kws:
        if kw.lower() in text_lower:
            return True

    # 3. Always accept official civic & infrastructure notices from local/civic feeds
    if getattr(art, "source_id", "") in ("nmmc_cidco", "ilove_navimumbai", "loksatta"):
        return True
    if getattr(art, "priority", 5) in (6, 7, 8):
        return True

    # 4. Core hard-news categories
    hard_news_categories = {
        "politics", "economy", "infrastructure", "civic",
        "safety", "accidents", "trains", "traffic", "water"
    }
    if art.category in hard_news_categories:
        return True

    # 5. Filter out routine sports, filler business, and general other
    return False
