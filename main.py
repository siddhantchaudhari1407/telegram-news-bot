#!/usr/bin/env python3
"""Telegram Daily News Bot - CLI entry point."""
import sys
import io
import logging
from pathlib import Path

# Force UTF-8 on Windows so emoji/Unicode prints correctly in all consoles
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if sys.stderr.encoding and sys.stderr.encoding.lower() != "utf-8":
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# Ensure project root is on the path when run directly
BASE_DIR = Path(__file__).parent
sys.path.insert(0, str(BASE_DIR))

LOG_DIR = BASE_DIR / "logs"
LOG_FILE = LOG_DIR / "bot.log"


def setup_logging() -> None:
    """Configure logging to console (INFO) and logs/bot.log (DEBUG)."""
    LOG_DIR.mkdir(exist_ok=True)

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)

    # Console handler — INFO and above
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_fmt = logging.Formatter("%(levelname)-8s %(name)s: %(message)s")
    console_handler.setFormatter(console_fmt)

    # File handler — DEBUG and above
    file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_fmt = logging.Formatter(
        "%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    file_handler.setFormatter(file_fmt)

    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)


def cmd_check_feeds() -> None:
    """Test all configured feed URLs and report results as a formatted table."""
    from bot.config import load_config
    from bot.collector import check_feeds

    config = load_config()
    sources_config = config["sources"]

    print("\n🔍 Checking RSS feeds for all priority sources...\n")
    results = check_feeds(sources_config)

    working = results["working"]
    failed = results["failed"]
    all_results = working + failed

    col_widths = {
        "prio": 4,
        "name": max(len("Source"), max((len(r["name"]) for r in all_results), default=0)),
        "focus": max(len("Focus"), max((len(str(r["section"])) for r in all_results), default=0)),
        "status": 7,
        "entries": 7,
    }

    header = (
        f"{'Prio':<{col_widths['prio']}}  "
        f"{'Source':<{col_widths['name']}}  "
        f"{'Focus':<{col_widths['focus']}}  "
        f"{'Status':<{col_widths['status']}}  "
        f"{'Entries':>{col_widths['entries']}}"
    )
    separator = "-" * len(header)
    print(separator)
    print(header)
    print(separator)

    for r in sorted(all_results, key=lambda x: x.get("priority", 99)):
        status_icon = "✅ OK" if r["status"] == "OK" else "❌ FAIL"
        prio_str = f"P{r.get('priority', '-')}"
        print(
            f"{prio_str:<{col_widths['prio']}}  "
            f"{r['name']:<{col_widths['name']}}  "
            f"{str(r['section']):<{col_widths['focus']}}  "
            f"{status_icon:<{col_widths['status']}}  "
            f"{r['entry_count']:>{col_widths['entries']}}"
        )

    print(separator)
    print(f"\n📊 Summary: {len(working)} working, {len(failed)} failed out of {len(all_results)} total sources.\n")
    if failed:
        print("⚠️ Failed feeds:")
        for r in failed:
            print(f"   • {r['name']}: {r['error']}")
        print()


def cmd_help() -> None:
    """Print usage information."""
    print("Usage: python main.py <command>")
    print()
    print("Commands:")
    print("  watch         Run continuous real-time monitor (polls every 1 min)")
    print("  run           Perform a single 1-minute poll and post new articles")
    print("  check-feeds   Test all 9 priority source feeds and report status")
    print("  setup         Validate Telegram bot token and channel admin status")
    print("  health        Show real-time polling stats, errors, and feed health")
    print()
    print("Examples:")
    print("  python main.py watch        # Run live 24/7 watcher loop")
    print("  python main.py run          # Run single poll (used by Task Scheduler)")
    print("  python main.py check-feeds  # Verify all RSS feeds are reachable")
    print()


def cmd_setup() -> None:
    """Validate Telegram bot token, check channel admin status, send test message."""
    from bot.config import load_config, get_env
    from bot.sender import get_me, get_chat_administrators, send_plain, TelegramError

    print("\n🔧 Running setup checks...\n")

    # Load env — will raise if BOT_TOKEN or CHANNEL_ID missing
    try:
        token      = get_env("BOT_TOKEN")
        channel_id = get_env("CHANNEL_ID")
    except EnvironmentError as exc:
        print(f"   ❌ Configuration error: {exc}")
        return

    if "your_telegram_bot_token" in token.lower():
        print("   ❌ BOT_TOKEN is still set to the placeholder in .env.")
        print("      → Please edit .env with your real bot token from @BotFather.")
        return

    if "your_channel_id" in channel_id.lower():
        print("   ❌ CHANNEL_ID is still set to the placeholder in .env.")
        print("      → Please edit .env with your real channel username (e.g. @mychannel) or numeric ID.")
        return

    # 1. Validate token
    print("1. Validating bot token...")
    try:
        bot_info = get_me(token)
        bot_name = bot_info.get("username", "unknown")
        print(f"   ✅ Token valid. Bot username: @{bot_name}")
    except TelegramError as exc:
        print(f"   ❌ Invalid token: {exc}")
        print("      → Check BOT_TOKEN in your .env file.")
        return
    except ConnectionError as exc:
        print(f"   ❌ Network error: {exc}")
        return

    bot_id = bot_info.get("id")

    # 2. Check bot is admin in the channel
    print(f"2. Checking bot is admin in channel {channel_id}...")
    try:
        admins = get_chat_administrators(token, channel_id)
        bot_admin = next((a for a in admins if a.get("user", {}).get("id") == bot_id), None)
        if bot_admin:
            status = bot_admin.get("status", "unknown")
            can_post = bot_admin.get("can_post_messages", True)  # True for creator
            if can_post or status == "creator":
                print(f"   ✅ Bot is admin (status: {status}) with post permission.")
            else:
                print(f"   ⚠️  Bot is admin (status: {status}) but 'can_post_messages' is False.")
                print("      → In channel settings → Administrators → edit bot → enable 'Post Messages'.")
        else:
            print("   ❌ Bot is NOT an administrator of this channel.")
            print("      → In Telegram: channel → Edit → Administrators → Add Administrator → search @" + bot_name)
    except TelegramError as exc:
        print(f"   ❌ Could not fetch admins: {exc}")
        print("      → Make sure CHANNEL_ID is correct (e.g. @mychannel or -100xxxxxxxxxx).")
        return
    except ConnectionError as exc:
        print(f"   ❌ Network error: {exc}")
        return

    # 3. Test message (ask first)
    print()
    print("3. Test message")
    print(f"   This will send a test message to: {channel_id}")
    try:
        answer = input("   Send test message? [y/N]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print("\n   Skipped (non-interactive mode).")
        return

    if answer != "y":
        print("   Skipped.")
        return

    try:
        from bot.utils import get_ist_now
        now = get_ist_now().strftime("%d %b %Y %H:%M IST")
        test_text = (
            f"✅ Daily News Bot test message\n"
            f"Sent at {now}\n\n"
            f"Setup is working correctly. This message confirms the bot "
            f"can post to this channel.\n\n"
            f"You can delete this message."
        )
        msg_id = send_plain(token, channel_id, test_text)
        print(f"   ✅ Test message sent! (message_id: {msg_id})")
        print()
        print("Setup complete. You're ready to run: python main.py run")
    except TelegramError as exc:
        print(f"   ❌ Failed to send test message: {exc}")
    except ConnectionError as exc:
        print(f"   ❌ Network error sending test message: {exc}")
    print()


def cmd_run() -> None:
    """Perform a single real-time poll and post any newly published articles."""
    from bot.config import load_config, get_env
    from bot.runner import poll_and_post_latest

    try:
        token      = get_env("BOT_TOKEN")
        channel_id = get_env("CHANNEL_ID")
    except EnvironmentError as exc:
        print(f"❌ Configuration error: {exc}")
        sys.exit(1)

    if "your_telegram_bot_token" in token.lower() or "your_channel_id" in channel_id.lower():
        print("❌ Configuration error: .env contains placeholder values.")
        print("   Please edit .env with your real BOT_TOKEN and CHANNEL_ID.")
        sys.exit(1)

    config = load_config()
    sources    = config["sources"]
    categories = config["categories"]

    print(f"\n🔍 Checking priority sources for fresh news...\n")
    posted = poll_and_post_latest(token, channel_id, sources, categories)
    if posted > 0:
        print(f"✅ Posted {posted} new breaking article(s) to {channel_id}.")
    else:
        print("ℹ️ Check complete. No new articles to post (or initial state seeded).")


def cmd_watch() -> None:
    """Continuously poll feeds every minute and post news as soon as published."""
    import time
    from datetime import datetime
    from bot.config import load_config, get_env
    from bot.runner import poll_and_post_latest

    try:
        token      = get_env("BOT_TOKEN")
        channel_id = get_env("CHANNEL_ID")
    except EnvironmentError as exc:
        print(f"❌ Configuration error: {exc}")
        sys.exit(1)

    config = load_config()
    sources    = config["sources"]
    categories = config["categories"]
    poll_sec = sources.get("settings", {}).get("poll_interval_seconds", 60)

    print(f"\n⚡ Real-Time News Watcher active! Polling every {poll_sec}s...")
    print(f"📢 Publishing to: {channel_id}")
    print("Press Ctrl+C to stop.\n")

    try:
        while True:
            posted = poll_and_post_latest(token, channel_id, sources, categories)
            if posted > 0:
                print(f"[{datetime.now().strftime('%H:%M:%S')}] Posted {posted} new article(s).")
            time.sleep(poll_sec)
    except KeyboardInterrupt:
        print("\n🛑 Watcher stopped by user.")


def cmd_health() -> None:
    """Show real-time polling stats, errors, and feed health summary."""
    from bot.config import load_config
    from bot.state import get_health_info
    from bot.collector import check_feeds
    from bot.utils import get_ist_now

    print("\n🩺 Real-Time News Bot Health Check\n")

    info = get_health_info()
    print(f"  Last poll time:    {info.get('last_poll_time', 'never')}")
    print(f"  Articles today:    {info.get('posted_today_count', 0)} posted")
    print(f"  Seen IDs cached:   {info.get('seen_count', 0)}")
    if info.get("last_error"):
        print(f"  Last error:        {info['last_error']}")
        print(f"  Last error time:   {info['last_error_time']}")
    else:
        print("  Last error:        none")

    now = get_ist_now()
    print(f"  Current time:      {now.strftime('%Y-%m-%d %H:%M:%S IST')}")

    print("\n  Priority Feed Health (live check):")
    config = load_config()
    results = check_feeds(config["sources"])
    working = len(results["working"])
    failed  = len(results["failed"])
    print(f"    {working}/{working + failed} priority feeds reachable")
    for f in sorted(results["working"], key=lambda x: x.get("priority", 99)):
        print(f"    ✅ P{f['priority']} {f['name']} ({f['entry_count']} entries)")
    for f in results["failed"]:
        print(f"    ❌ P{f['priority']} {f['name']}: {str(f['error'])[:50]}")
    print()


if __name__ == "__main__":
    setup_logging()

    command = sys.argv[1] if len(sys.argv) > 1 else "help"

    commands = {
        "check-feeds": cmd_check_feeds,
        "watch":       cmd_watch,
        "run":         cmd_run,
        "setup":       cmd_setup,
        "health":      cmd_health,
        "help":        cmd_help,
        "--help":      cmd_help,
        "-h":          cmd_help,
    }

    if command in commands:
        commands[command]()
    else:
        print(f"Unknown command: '{command}'")
        print()
        cmd_help()
        sys.exit(1)
