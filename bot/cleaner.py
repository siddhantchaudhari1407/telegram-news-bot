"""Phase 2 – Deduplication and classification refinements.

Headline deduplication uses a simple but effective token-overlap similarity
(Jaccard on word sets).  No external libraries needed.

All classification is keyword-based from categories.yaml.  Results are
best-effort; the bot never claims to verify facts.
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

SIMILARITY_THRESHOLD = 0.55   # Jaccard similarity above which headlines merge


def deduplicate(articles: list["Article"]) -> list["Article"]:
    """Remove near-identical headlines within each section.

    Algorithm (per section):
      1. Normalise each headline to a set of meaningful tokens.
      2. Compare every pair; if Jaccard similarity >= THRESHOLD, treat as same story.
      3. Keep the article carried by the most publishers (most entry_count weight),
         merging the rest into other_publishers.
      4. Within a tie, prefer the earliest-published version.

    Cross-section dedup is intentionally NOT done: the same story may be
    legitimately under both India and Mumbai sections.
    """
    from collections import defaultdict

    # Group by section
    by_section: dict[str, list["Article"]] = defaultdict(list)
    for art in articles:
        by_section[art.section].append(art)

    result: list["Article"] = []
    for section, arts in by_section.items():
        result.extend(_dedup_section(arts))

    return result


def reclassify(articles: list["Article"], categories_config: dict) -> list["Article"]:
    """Re-run classification on every article (idempotent).

    Useful after dedup merges descriptions from multiple publishers.
    Also normalises category key → human-readable display name.
    """
    for art in articles:
        combined = f"{art.headline} {art.description}"
        art.category = _classify(combined, categories_config)
        art.is_developing = _is_developing(combined, categories_config)
    return articles


def sort_articles(articles: list["Article"]) -> list["Article"]:
    """Sort articles within each section by publisher count desc, then time desc.

    More publishers = more important story → shown first.
    """
    return sorted(
        articles,
        key=lambda a: (a.section, -(len(a.other_publishers) + 1), a.published_time),
        reverse=False,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_STOPWORDS = frozenset(
    "a an the and or but in on at to of for is are was were be been "
    "being have has had do does did will would could should may might "
    "shall can s its it this that with from by as into".split()
)


def _tokenise(headline: str) -> frozenset[str]:
    """Lower-case, strip punctuation, remove stop-words, return frozen word set."""
    tokens = re.sub(r"[^a-z0-9\s]", " ", headline.lower()).split()
    return frozenset(t for t in tokens if t not in _STOPWORDS and len(t) > 1)


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a or not b:
        return 0.0
    intersection = len(a & b)
    union = len(a | b)
    return intersection / union if union else 0.0


def _dedup_section(arts: list["Article"]) -> list["Article"]:
    """Cluster near-identical headlines; keep one representative per cluster."""
    if not arts:
        return []

    token_sets = [_tokenise(a.headline) for a in arts]
    n = len(arts)

    # Union-Find for clustering
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
            if _jaccard(token_sets[i], token_sets[j]) >= SIMILARITY_THRESHOLD:
                union(i, j)

    # Build clusters: root → list of indices
    clusters: dict[int, list[int]] = {}
    for i in range(n):
        root = find(i)
        clusters.setdefault(root, []).append(i)

    deduped: list["Article"] = []
    for indices in clusters.values():
        if len(indices) == 1:
            deduped.append(arts[indices[0]])
            continue

        # Pick representative: most other_publishers first, then earliest time
        cluster_arts = [arts[i] for i in indices]
        representative = max(
            cluster_arts,
            key=lambda a: (len(a.other_publishers), -a.published_time.timestamp()),
        )

        # Merge all publishers into representative
        all_publishers: set[str] = set(representative.other_publishers)
        all_publishers.add(representative.publisher)
        for art in cluster_arts:
            if art is representative:
                continue
            all_publishers.add(art.publisher)
            all_publishers.update(art.other_publishers)
            # Merge descriptions (take longest non-empty one)
            if len(art.description) > len(representative.description):
                representative.description = art.description

        all_publishers.discard(representative.publisher)
        representative.other_publishers = sorted(all_publishers)

        merged_count = len(indices)
        if merged_count > 1:
            logger.debug(
                "Dedup [%s]: merged %d articles → '%s' (also: %s)",
                representative.section,
                merged_count,
                representative.headline[:60],
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
    """Filter out noise, routine match scores, and low-impact fluff.

    Criteria for important news:
    1. Reject if headline matches noise_keywords (e.g. NFL, fantasy, box office, celebrity).
    2. Accept if matches high_importance_keywords (e.g. PM, cabinet, elections, SC, RBI, disaster).
    3. Accept official civic & infrastructure notices (NMMC, CIDCO, Navi Mumbai infrastructure).
    4. Accept core hard-news categories (politics, economy, infrastructure, civic, safety, accidents, trains, water).
    5. Filter out routine sports match scores, entertainment, and non-essential filler.
    """
    settings = categories_config.get("settings", {})
    noise_kws = settings.get("noise_keywords", [])
    high_kws = settings.get("high_importance_keywords", [])

    text_lower = f"{art.headline} {art.description}".lower()

    # 1. Noise check (sports fluff, entertainment gossip)
    for kw in noise_kws:
        if kw.lower() in text_lower:
            return False

    # 2. High importance keywords override
    for kw in high_kws:
        if kw.lower() in text_lower:
            return True

    # 3. Always accept official civic & infrastructure notices from P7 / P8
    if getattr(art, "priority", 5) in (7, 8):
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

