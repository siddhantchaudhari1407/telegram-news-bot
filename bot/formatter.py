"""Phase 3 – Telegram message formatter and chunker.

Rules:
- Each story: headline (bold), short description (~200 chars from feed, not rewritten),
  publisher + other_publishers, published time, link.
- Stories ranked by publisher count (multi-publisher first) within each section.
- Cap per section configured in sources.yaml (default 20).
- Each Telegram message stays under MAX_MSG_CHARS characters.
- Never split a story across two messages.
- Escape all text for Telegram MarkdownV2.
- Sections with no stories get a "No stories found" notice.
- Final chunk always carries the disclaimer footer.
"""
from __future__ import annotations

import re
import logging
from collections import defaultdict
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bot.collector import Article

from bot.cleaner import display_category
from bot.utils import format_ist_time

logger = logging.getLogger(__name__)

MAX_MSG_CHARS = 4000   # Telegram limit is 4096; keep headroom for safety
DESCRIPTION_MAX = 200  # chars to trim feed description to

# Telegram MarkdownV2 special characters that must be escaped
_MD2_SPECIAL = r"\_*[]()~`>#+-=|{}.!"


def escape_md2(text: str) -> str:
    """Escape a plain-text string for Telegram MarkdownV2."""
    # Each special character must be preceded by a backslash
    return re.sub(r"([" + re.escape(_MD2_SPECIAL) + r"])", r"\\\1", text)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

DISCLAIMER = (
    "\n_Coverage is based on the feeds I could reach, "
    "not a complete list of everything that happened\\._"
)


def format_digest(
    articles: list["Article"],
    sources_config: dict,
    date_str: str,
    is_catchup: bool = False,
) -> list[str]:
    """Build the full list of Telegram MarkdownV2 message chunks for the digest.

    Args:
        articles:       cleaned, deduped, sorted Article objects.
        sources_config: loaded sources.yaml dict (for max_stories_per_section).
        date_str:       human-readable date label, e.g. "Wednesday, 28 Oct 2026".
        is_catchup:     if True, prepend a catch-up notice to the first message.

    Returns:
        List of strings, each ≤ MAX_MSG_CHARS characters, safe for Telegram
        sendMessage with parse_mode="MarkdownV2".
    """
    max_per_section: int = (
        sources_config.get("settings", {}).get("max_stories_per_section", 20)
    )

    # Group articles by section, preserving insertion order
    by_section: dict[str, list[Article]] = defaultdict(list)
    for art in articles:
        by_section[art.section].append(art)

    # Determine section order: preserve sources.yaml order, then any extras
    section_order: list[str] = list(sources_config.get("sections", {}).keys())
    for sec in by_section:
        if sec not in section_order:
            section_order.append(sec)

    # --- Build header block ---
    catchup_line = (
        "\n⚠️ *Catch\\-up report \\(sent late\\)*\n"
        if is_catchup else ""
    )
    header = (
        f"📰 *Daily News Digest*\n"
        f"{escape_md2(date_str)}\n"
        f"{catchup_line}"
    )

    # --- Build per-section blocks (list of story strings) ---
    # Each story_block is a self-contained string ready to be stitched.
    all_blocks: list[str] = [header]

    for section in section_order:
        sec_arts = by_section.get(section, [])

        # Section header
        sec_header = f"\n{'═' * 28}\n*{escape_md2(section.upper())}*\n{'═' * 28}\n"
        all_blocks.append(sec_header)

        if not sec_arts:
            all_blocks.append(
                "_No stories found in the configured sources\\._\n"
            )
            continue

        # Cap to max and format each story
        for art in sec_arts[:max_per_section]:
            all_blocks.append(_format_story(art))

    # Attach disclaimer to the last real block
    all_blocks.append(DISCLAIMER)

    # --- Chunk blocks into messages ≤ MAX_MSG_CHARS ---
    return _chunk_blocks(all_blocks)


def _format_story(art: "Article") -> str:
    """Render a single Article as a MarkdownV2 story block."""
    # Headline (bold) — strip trailing " - Publisher" suffix common in Google News
    headline = _strip_source_suffix(art.headline)
    headline_md = f"*{escape_md2(headline)}*"

    # Category + developing tag
    cat_label = display_category(art.category)
    dev_label = " \\[Developing\\]" if art.is_developing else ""
    cat_line = f"\\[{escape_md2(cat_label)}\\]{dev_label}"

    # Description: feed's own text, trimmed to DESCRIPTION_MAX chars, no rewriting
    desc_line = ""
    if art.description:
        desc = art.description.strip()
        if len(desc) > DESCRIPTION_MAX:
            # Trim at last word boundary before the limit
            desc = desc[:DESCRIPTION_MAX].rsplit(" ", 1)[0] + "…"
        desc_line = f"_{escape_md2(desc)}_\n"

    # Publisher line
    pub = art.publisher
    if art.other_publishers:
        pub += " \\+ " + " \\+ ".join(escape_md2(p) for p in art.other_publishers)
    else:
        pub = escape_md2(pub)

    time_str = escape_md2(format_ist_time(art.published_time))
    url = art.url  # URLs are not MarkdownV2-escaped inside []()

    story = (
        f"{cat_line}\n"
        f"{headline_md}\n"
        f"{desc_line}"
        f"{pub} \\| {time_str}\n"
        f"[Read more]({url})\n"
    )
    return story


def _strip_source_suffix(headline: str) -> str:
    """Remove trailing ' - Publisher' from Google News headlines."""
    return re.sub(r"\s+[-–]\s+[A-Za-z][A-Za-z0-9 &'.,]+$", "", headline).strip()


def _chunk_blocks(blocks: list[str]) -> list[str]:
    """Pack blocks into messages, each ≤ MAX_MSG_CHARS.

    Never splits a single block across messages.  If a single block exceeds
    MAX_MSG_CHARS on its own (pathological), it is placed alone in its chunk
    with a truncation notice appended.
    """
    chunks: list[str] = []
    current = ""

    for block in blocks:
        # Pathological: single block too big — truncate at word boundary
        if len(block) > MAX_MSG_CHARS:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(block[: MAX_MSG_CHARS - 30].rsplit(" ", 1)[0] + "…\n\\[truncated\\]")
            continue

        if len(current) + len(block) <= MAX_MSG_CHARS:
            current += block
        else:
            if current:
                chunks.append(current)
            current = block

    if current:
        chunks.append(current)

    return chunks


def format_realtime_alert(art: "Article") -> str:
    """Format an individual real-time news alert in Telegram MarkdownV2."""
    from bot.utils import get_ist_now

    icons = {
        1: "🚨", 2: "📌", 3: "📰", 4: "🌍",
        5: "🏙️", 6: "🚩", 7: "📍", 8: "🏛️", 9: "📻"
    }
    icon = icons.get(art.priority, "📌")

    # Relative time calculation
    now_ist = get_ist_now()
    delta_sec = (now_ist - art.published_time).total_seconds()
    mins_ago = max(0, int(delta_sec / 60))

    if mins_ago <= 1:
        time_tag = "🔴 *JUST IN \\(Just now\\)*"
        rel_str = "Just now"
    elif mins_ago < 60:
        time_tag = f"🔴 *JUST IN \\({mins_ago}m ago\\)*"
        rel_str = f"{mins_ago}m ago"
    else:
        hours = mins_ago // 60
        time_tag = f"⚡ *UPDATE \\({hours}h ago\\)*"
        rel_str = f"{hours}h ago"

    priority_str = f"Priority {art.priority}"
    source_header = f"{icon} *[{escape_md2(art.publisher)} • {escape_md2(priority_str)}]*"
    if art.is_developing:
        source_header += " _\\[Developing\\]_"

    headline = _strip_source_suffix(art.headline)
    headline_md = f"*{escape_md2(headline)}*"

    desc_md = ""
    if art.description:
        desc = art.description.strip()
        if len(desc) > DESCRIPTION_MAX:
            desc = desc[:DESCRIPTION_MAX].rsplit(" ", 1)[0] + "…"
        desc_md = f"_{escape_md2(desc)}_\n\n"

    cat = display_category(art.category)
    cat_line = f"🏷️ *Category:* {escape_md2(cat)}"
    if art.tags:
        cat_line += f"  {escape_md2(art.tags)}"

    time_str = escape_md2(format_ist_time(art.published_time))
    rel_str_escaped = escape_md2(f"({rel_str})")
    url = art.url

    return (
        f"{time_tag}\n"
        f"{source_header}\n\n"
        f"{headline_md}\n\n"
        f"{desc_md}"
        f"{cat_line}\n"
        f"🕒 {time_str} {rel_str_escaped}\n"
        f"👉 [Read more]({url})"
    )



def format_digest_plain(
    articles: list["Article"],
    sources_config: dict,
    date_str: str,
    is_catchup: bool = False,
) -> str:
    """Return a human-readable plain-text digest for terminal preview."""
    max_per_section: int = (
        sources_config.get("settings", {}).get("max_stories_per_section", 20)
    )
    by_section: dict[str, list[Article]] = defaultdict(list)
    for art in articles:
        by_section[art.section].append(art)

    section_order: list[str] = list(sources_config.get("sections", {}).keys())
    for sec in by_section:
        if sec not in section_order:
            section_order.append(sec)

    lines: list[str] = []
    catchup = " [CATCH-UP REPORT - SENT LATE]" if is_catchup else ""
    lines.append(f"📰 Daily News Digest{catchup}")
    lines.append(date_str)
    lines.append("")

    for section in section_order:
        sec_arts = by_section.get(section, [])
        lines.append("═" * 50)
        lines.append(section.upper())
        lines.append("═" * 50)

        if not sec_arts:
            lines.append("  No stories found in the configured sources.")
            lines.append("")
            continue

        for i, art in enumerate(sec_arts[:max_per_section], 1):
            headline = _strip_source_suffix(art.headline)
            cat_label = display_category(art.category)
            dev = " [DEVELOPING]" if art.is_developing else ""
            desc = art.description.strip()
            if len(desc) > DESCRIPTION_MAX:
                desc = desc[:DESCRIPTION_MAX].rsplit(" ", 1)[0] + "…"

            pub = art.publisher
            if art.other_publishers:
                pub += " + " + " + ".join(art.other_publishers)

            lines.append(f"{i:>2}. [{cat_label}]{dev}")
            lines.append(f"    {headline}")
            if desc:
                lines.append(f"    {desc}")
            lines.append(f"    {pub} | {format_ist_time(art.published_time)}")
            lines.append(f"    {art.url}")
            lines.append("")

    lines.append(
        "Coverage is based on the feeds I could reach, "
        "not a complete list of everything that happened."
    )
    return "\n".join(lines)
