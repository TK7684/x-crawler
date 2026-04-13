#!/usr/bin/env python3
"""
X (Twitter) Scraper — Scheduler + Database

Same anti-detection approach as FB crawler:
- Real browser (no headless)
- Random intervals (30-180 min)
- Max 8 targets/day
- Quiet hours (2-5 AM)
- 6-hour cooldown per target

Usage:
  ./venv/bin/python3 run_scheduler.py --init          # Initialize DB
  ./venv/bin/python3 run_scheduler.py --add <url>     # Add target to track
  ./venv/bin/python3 run_scheduler.py --targets        # List all targets
  ./venv/bin/python3 run_scheduler.py --daemon         # Start daemon
  ./venv/bin/python3 run_scheduler.py --db-stats       # Database stats
"""

import json
import os
import random
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

for _p in [
    os.environ.get("CRAWLER_DIR", ""),
    os.path.expanduser("~/.openclaw/workspace/crawlers"),
    os.path.expanduser("~/crawlers"),
]:
    if _p and os.path.isfile(os.path.join(_p, "crawler_db.py")):
        sys.path.insert(0, _p)
        break
import crawler_db

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
LOG_FILE = BASE_DIR / "scheduler.log"

MAX_SCRAPES_PER_DAY = 8
MIN_INTERVAL_MIN = 30
MAX_INTERVAL_MIN = 180
BREAK_EVERY_N = 3
BREAK_MIN_MIN = 60
BREAK_MAX_MIN = 180
QUIET_START = 2
QUIET_END = 5


def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")


def _twitter_external_id(url):
    """Extract handle or list id from an x.com URL."""
    from urllib.parse import urlparse
    path = urlparse(url).path.strip('/')
    if '/lists/' in url or path.startswith('i/lists/'):
        return path.rsplit('/', 1)[-1]
    return path.split('/')[0]


# ── Runner ──────────────────────────────────────────────────────────

def run_scraper(target_url):
    import tempfile

    venv_python = BASE_DIR / "venv" / "bin" / "python3"
    scraper = BASE_DIR / "scraper.py"
    limit = random.randint(20, 60)

    # Pass known tweet IDs for incremental scraping
    sid = crawler_db.get_source_id("twitter", _twitter_external_id(target_url))
    known = crawler_db.get_known_post_ids(sid) if sid else set()
    known_file = None
    cmd = [str(venv_python), str(scraper),
           "--url", target_url, "--headless",
           "--limit", str(limit),
           "--replies", "--images", "--export", "json"]

    if known:
        known_file = tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False, dir=str(BASE_DIR))
        json.dump(list(known), known_file)
        known_file.close()
        cmd.extend(["--known-ids-file", known_file.name])
        log(f"  🔧 Scraping {limit} posts (incremental, {len(known)} known)...")
    else:
        log(f"  🔧 Scraping {limit} posts (first run)...")

    try:
        result = subprocess.run(cmd, capture_output=True, text=True,
                                timeout=900, cwd=str(BASE_DIR))
    finally:
        if known_file:
            Path(known_file.name).unlink(missing_ok=True)

    if result.returncode != 0:
        log(f"  ❌ Error: {result.stderr[-500:]}")
        return []

    # Find latest JSON in target dir
    target = target_url.rstrip("/").split("/")[-1]
    target_dir = DATA_DIR / target
    if not target_dir.exists():
        return []

    jf = sorted(target_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    for f in jf:
        if "_summary" in f.name:
            continue
        try:
            data = json.load(f.open())
            if isinstance(data, list):
                return data
        except:
            continue
    return []


def cleanup_old_exports(target_url, keep=5):
    """Keep only the most recent N export files per target."""
    target = target_url.rstrip("/").split("/")[-1]
    target_dir = DATA_DIR / target
    if not target_dir.exists():
        return 0
    all_files = sorted(target_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    json_files = [f for f in all_files if "_summary" not in f.name]
    removed = 0
    for old in json_files[keep:]:
        stem = old.stem
        for sibling in target_dir.glob(f"{stem}*"):
            try:
                sibling.unlink()
                removed += 1
            except:
                pass
    return removed


def should_take_break(session_count):
    return session_count > 0 and session_count % BREAK_EVERY_N == 0


# ── Scheduler ───────────────────────────────────────────────────────

def scrape_one():
    hour = datetime.now().hour
    if QUIET_START <= hour < QUIET_END:
        log(f"😴 Quiet hours ({QUIET_START}-{QUIET_END} AM)")
        return False

    if crawler_db.get_scrapes_today("twitter") >= MAX_SCRAPES_PER_DAY:
        log(f"⏸️ Daily limit ({MAX_SCRAPES_PER_DAY})")
        return False

    picked = crawler_db.pick_next_source("twitter", host=os.environ.get("CRAWLER_HOST"))
    if not picked:
        log("⚠️ No targets available")
        return False
    source_id, url, external_id, name = picked

    started = datetime.now().isoformat()
    log(f"📡 [{name or url}] Starting...")

    try:
        posts = run_scraper(url)
        if not posts:
            log("  ⚠️ No posts")
            crawler_db.log_scrape(source_id, "twitter", started, 0, 0, 0, "no_posts")
            return True
        new_p, new_t, skipped = crawler_db.save_posts(source_id, "twitter", posts)
        log(f"  ✅ {len(posts)} posts ({new_p} new, {skipped} known), {new_t} new replies")
        crawler_db.log_scrape(source_id, "twitter", started, new_p, len(posts), new_t,
                              posts_skipped=skipped)
        removed = cleanup_old_exports(url, keep=5)
        if removed:
            log(f"  🧹 Cleaned {removed} old export files")
        return True
    except subprocess.TimeoutExpired:
        log("  ⏰ Timeout")
        crawler_db.log_scrape(source_id, "twitter", started, 0, 0, 0, "error", "Timeout")
        return True
    except Exception as e:
        log(f"  ❌ {e}")
        crawler_db.log_scrape(source_id, "twitter", started, 0, 0, 0, "error", str(e)[:500])
        return True


def run_daemon():
    log("🤖 X Scraper daemon started (anti-detection mode)")
    log(f"   Max scrapes/day: {MAX_SCRAPES_PER_DAY}")
    log(f"   Interval: {MIN_INTERVAL_MIN}-{MAX_INTERVAL_MIN} min")
    log(f"   Quiet hours: {QUIET_START}-{QUIET_END} AM")

    session_count = 0
    while True:
        if scrape_one():
            session_count += 1

            if should_take_break(session_count):
                break_min = random.randint(BREAK_MIN_MIN, BREAK_MAX_MIN)
                log(f"  🛑 Long break ({break_min} min)")
                time.sleep(break_min * 60)

        interval = random.randint(MIN_INTERVAL_MIN, MAX_INTERVAL_MIN) * 60
        log(f"  ⏳ Next scrape in {interval // 60} min")
        time.sleep(interval)


def main():
    import argparse
    parser = argparse.ArgumentParser(description="X Scraper Scheduler")
    parser.add_argument("--init", action="store_true")
    parser.add_argument("--add", type=str, metavar="URL")
    parser.add_argument("--add-name", type=str, default="")
    parser.add_argument("--targets", action="store_true")
    parser.add_argument("--db-stats", action="store_true")
    parser.add_argument("--daemon", action="store_true")
    args = parser.parse_args()

    crawler_db.init_db()

    if args.targets:
        rows = crawler_db.list_sources("twitter")
        if not rows:
            print("No targets. Use --add <url> to add targets.")
        else:
            print(f"\n📊 Tracked Targets ({len(rows)}):")
            for row in rows:
                # (id, platform, source_type, external_id, url, name, total_posts, total_threads, last_scraped)
                _id, _plat, stype, ext, url, name, total_p, _total_t, last = row
                print(f"  {name or url} [{stype}]: {total_p} posts (last: {last or 'never'})")
    elif args.add:
        name = args.add_name or args.add.rstrip("/").split("/")[-1]
        ttype = "list" if "/lists/" in args.add else "profile"
        crawler_db.ensure_source(
            "twitter", ttype, _twitter_external_id(args.add), args.add, name
        )
        log(f"Added target: {args.add} ({name})")
    elif args.db_stats:
        crawler_db.show_db_stats("twitter")
    elif args.daemon:
        run_daemon()
    else:
        scrape_one()


if __name__ == "__main__":
    main()
