"""Main run orchestrator for the news bot.

Ties together: fetch → clean → format → send → state.
Handles catch-up detection, resume-without-duplicate, and failure alerts.
"""
from __future__ import annotations

import logging
from datetime import date

logger = logging.getLogger(__name__)


def run_digest(token: str, channel_id: str, sources_config: dict, categories_config: dict) -> bool:
    """Fetch yesterday's news, format it, and post to Telegram.

    Returns True on full success, False if any part failed.

    - Detects catch-up (called later than scheduled) and labels accordingly.
    - Resumes a partial run (sent some chunks, then crashed) without duplicates.
    - Sends a failure alert to the channel if all retries exhausted.
    """
    from bot.collector  import fetch_articles
    from bot.cleaner    import deduplicate, reclassify, sort_articles
    from bot.formatter  import format_digest
    from bot.sender     import send_message, send_plain, TelegramError
    from bot.state      import (
        is_already_sent, get_pending_parts,
        record_part_sent, mark_success, record_error,
    )
    from bot.utils import get_yesterday_ist, get_ist_now

    target_date: date = get_yesterday_ist()
    date_str = target_date.strftime("%A, %d %b %Y")

    # --- Duplicate guard ---
    if is_already_sent(target_date):
        logger.info("Digest for %s already delivered; skipping.", target_date)
        return True

    # --- Catch-up detection ---
    now_ist = get_ist_now()
    # If we are running more than 30 minutes after 08:00 IST, label as catch-up
    scheduled_hour, scheduled_minute = 8, 0
    is_catchup = (
        now_ist.hour > scheduled_hour
        or (now_ist.hour == scheduled_hour and now_ist.minute > scheduled_minute + 30)
    )
    if is_catchup:
        logger.info("Catch-up run detected (now=%02d:%02d IST).", now_ist.hour, now_ist.minute)

    # --- Fetch and process ---
    logger.info("Fetching articles for %s ...", target_date)
    try:
        raw      = fetch_articles(sources_config, categories_config, target_date=target_date)
        deduped  = deduplicate(raw)
        articles = sort_articles(reclassify(deduped, categories_config))
        logger.info("Ready: %d articles after dedup.", len(articles))
    except Exception as exc:
        msg = f"Failed to fetch/process articles: {exc}"
        logger.error(msg)
        record_error(msg)
        _try_alert(token, channel_id, f"⚠️ News bot error ({target_date}): {msg}")
        return False

    # --- Format ---
    chunks = format_digest(articles, sources_config, date_str, is_catchup=is_catchup)
    total  = len(chunks)
    logger.info("Formatted into %d Telegram chunks.", total)

    # --- Determine which parts still need sending (resume support) ---
    pending = get_pending_parts(target_date, total)
    if not pending:
        logger.info("All parts already recorded as sent; marking success.")
        mark_success(target_date)
        return True

    logger.info("Parts to send: %s (of %d total)", pending, total)

    # --- Send each pending chunk ---
    failed_parts: list[int] = []
    for idx in pending:
        text = chunks[idx]
        try:
            msg_id = send_message(token, channel_id, text)
            record_part_sent(target_date, idx, total, msg_id)
            logger.info("Sent chunk %d/%d (msg_id=%d).", idx + 1, total, msg_id)
        except TelegramError as exc:
            logger.error("Telegram error on chunk %d: %s", idx + 1, exc)
            failed_parts.append(idx)
            record_error(str(exc))
        except ConnectionError as exc:
            logger.error("Network failure on chunk %d after all retries: %s", idx + 1, exc)
            failed_parts.append(idx)
            record_error(str(exc))

    # --- Outcome ---
    if not failed_parts:
        mark_success(target_date)
        logger.info("Digest for %s fully delivered (%d chunks).", target_date, total)
        return True
    else:
        err_msg = (
            f"Digest for {target_date} incomplete: "
            f"{len(failed_parts)} of {total} chunks failed to send."
        )
        logger.error(err_msg)
        record_error(err_msg)
        _try_alert(
            token, channel_id,
            f"⚠️ Daily News Bot: {len(failed_parts)} chunk(s) failed for {target_date}. "
            f"Re-run to resume."
        )
        return False


def poll_and_post_latest(
    token: str,
    channel_id: str,
    sources_config: dict,
    categories_config: dict,
) -> int:
    """Check all priority feeds for new articles and post attributed summaries.

    Returns the number of new articles posted.
    """
    from bot.collector import fetch_latest_articles
    from bot.cleaner import deduplicate, is_important_story, are_headlines_duplicate
    from bot.formatter import format_realtime_alert
    from bot.sender import send_message, send_photo, TelegramError
    from bot.state import (
        get_seen_ids, mark_articles_seen,
        get_seen_headlines, record_posted_headline,
        record_poll_run, record_error,
    )

    seen_ids = get_seen_ids()
    is_first_run = len(seen_ids) == 0

    logger.info("Polling feeds for latest articles (seen count=%d)...", len(seen_ids))
    try:
        new_articles = fetch_latest_articles(
            sources_config,
            categories_config,
            seen_ids=seen_ids,
        )
    except Exception as exc:
        msg = f"Failed to fetch latest articles: {exc}"
        logger.error(msg)
        record_error(msg)
        return 0

    # If first run: seed current feed entries into seen_ids so we don't spam historical news
    if is_first_run:
        all_ids = [a.id for a in new_articles] + [a.url for a in new_articles]
        mark_articles_seen(all_ids)
        record_poll_run(articles_posted=0)
        logger.info(
            "Initialized state: marked %d current articles as seen. Will post new arrivals from next poll.",
            len(new_articles),
        )
        return 0

    if not new_articles:
        logger.info("No new articles found.")
        record_poll_run(articles_posted=0)
        return 0

    import time

    # Filter for IMPORTANT stories only (discard sports fluff, entertainment gossip, non-essential filler)
    important_articles = [a for a in new_articles if is_important_story(a, categories_config)]
    logger.info("Filtered %d raw articles down to %d important stories.", len(new_articles), len(important_articles))

    # Mark non-important articles as seen so they are discarded permanently
    non_important = [a for a in new_articles if not is_important_story(a, categories_config)]
    if non_important:
        mark_articles_seen([a.id for a in non_important] + [a.url for a in non_important])

    if not important_articles:
        logger.info("No high-importance articles to post in this check.")
        record_poll_run(articles_posted=0)
        return 0

    # Deduplicate near-identical breaking headlines globally across all feeds
    deduped = deduplicate(important_articles, by_section=False)
    logger.info("Found %d unique important articles across feeds in this batch.", len(deduped))

    # Cross-poll duplicate guard: check against previously posted headlines
    seen_headlines = get_seen_headlines()
    non_repeat_articles = []
    for art in deduped:
        is_repeat = False
        for prev_h in seen_headlines:
            if are_headlines_duplicate(art.headline, prev_h):
                logger.info(
                    "Skipping duplicate story: '%s' matches previously posted '%s'",
                    art.headline[:40], prev_h[:40],
                )
                mark_articles_seen([art.id, art.url])
                is_repeat = True
                break
        if not is_repeat:
            non_repeat_articles.append(art)

    if not non_repeat_articles:
        logger.info("All candidates in this cycle were duplicates of previously posted news.")
        record_poll_run(articles_posted=0)
        return 0

    # Cap to at most 5 important articles per poll to avoid channel spam and rate limits
    MAX_PER_POLL = 5
    to_post = non_repeat_articles[:MAX_PER_POLL]
    excess = non_repeat_articles[MAX_PER_POLL:]
    if excess:
        mark_articles_seen([a.id for a in excess] + [a.url for a in excess])

    posted_count = 0
    for art in to_post:
        msg_text = format_realtime_alert(art)
        try:
            if art.image_url:
                try:
                    msg_id = send_photo(token, channel_id, art.image_url, caption=msg_text)
                    logger.info("Sent photo alert for [%s] '%s' (msg_id=%d)", art.publisher, art.headline[:40], msg_id)
                except Exception as img_exc:
                    logger.warning("Photo send failed (%s), falling back to text: %s", art.image_url, img_exc)
                    msg_id = send_message(token, channel_id, msg_text)
            else:
                msg_id = send_message(token, channel_id, msg_text)

            mark_articles_seen([art.id, art.url])
            record_posted_headline(art.headline)
            posted_count += 1
            logger.info(
                "Posted [%s • P%d] '%s' (msg_id=%d)",
                art.publisher, art.priority, art.headline[:50], msg_id,
            )
            time.sleep(2.0)  # polite spacing to avoid Telegram 429
        except TelegramError as exc:
            logger.error("Telegram error sending article '%s': %s", art.headline[:40], exc)
            record_error(str(exc))
        except ConnectionError as exc:
            logger.error("Network error sending article '%s': %s", art.headline[:40], exc)
            record_error(str(exc))

    record_poll_run(articles_posted=posted_count)
    return posted_count


def _try_alert(token: str, channel_id: str, message: str) -> None:
    """Best-effort: send a plain-text alert. Silently ignores failures."""
    from bot.sender import send_plain
    try:
        send_plain(token, channel_id, message)
        logger.info("Alert sent: %s", message)
    except Exception as exc:
        logger.warning("Could not send failure alert: %s", exc)
