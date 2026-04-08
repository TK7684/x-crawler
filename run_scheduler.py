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
import random
import sqlite3
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
DB_FILE = BASE_DIR / "x_scraper.db"
LOG_FILE = BASE_DIR / "scheduler.log"

MAX_SCRAPES_PER_DAY = 24
MIN_INTERVAL_MIN = 15
MAX_INTERVAL_MIN = 90
BREAK_EVERY_N = 3
BREAK_MIN_MIN = 20
BREAK_MAX_MIN = 60
QUIET_START = 2
QUIET_END = 5


# ── Database ────────────────────────────────────────────────────────

def init_db():
    conn = sqlite3.connect(str(DB_FILE))
    c = conn.cursor()
    c.executescript("""
        CREATE TABLE IF NOT EXISTS targets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            target_url TEXT UNIQUE,
            target_name TEXT DEFAULT '',
            target_type TEXT DEFAULT 'profile',
            last_scraped TEXT,
            total_posts INTEGER DEFAULT 0,
            total_replies INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS posts (
            post_id TEXT PRIMARY KEY,
            target_url TEXT NOT NULL,
            post_url TEXT,
            author TEXT DEFAULT '',
            author_handle TEXT DEFAULT '',
            text TEXT DEFAULT '',
            timestamp TEXT DEFAULT '',
            replies INTEGER DEFAULT 0,
            reposts INTEGER DEFAULT 0,
            likes INTEGER DEFAULT 0,
            image_urls TEXT DEFAULT '[]',
            video_url TEXT DEFAULT '',
            image_content TEXT DEFAULT '[]',
            scraped_at TEXT,
            FOREIGN KEY (target_url) REFERENCES targets(target_url)
        );
        CREATE INDEX IF NOT EXISTS idx_posts_target ON posts(target_url);
        CREATE INDEX IF NOT EXISTS idx_posts_date ON posts(scraped_at);

        CREATE TABLE IF NOT EXISTS tweet_replies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id TEXT NOT NULL,
            target_url TEXT NOT NULL,
            author TEXT DEFAULT '',
            author_handle TEXT DEFAULT '',
            text TEXT DEFAULT '',
            timestamp TEXT DEFAULT '',
            scraped_at TEXT,
            FOREIGN KEY (post_id) REFERENCES posts(post_id)
        );

        CREATE TABLE IF NOT EXISTS scrape_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            target_url TEXT,
            started_at TEXT,
            finished_at TEXT,
            posts_new INTEGER DEFAULT 0,
            posts_total INTEGER DEFAULT 0,
            replies_new INTEGER DEFAULT 0,
            status TEXT DEFAULT 'success',
            error TEXT DEFAULT ''
        );
    """)
    conn.commit()
    conn.close()
    log("Database initialized")


def add_target(url, name="", target_type="profile"):
    conn = sqlite3.connect(str(DB_FILE))
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO targets (target_url, target_name, target_type) VALUES (?, ?, ?)",
              (url, name, target_type))
    conn.commit()
    conn.close()
    log(f"Added target: {url} ({name})")


def list_targets():
    conn = sqlite3.connect(str(DB_FILE))
    c = conn.cursor()
    c.execute("SELECT target_url, target_name, target_type, total_posts, last_scraped FROM targets ORDER BY total_posts DESC")
    rows = c.fetchall()
    if not rows:
        print("No targets. Use --add <url> to add targets.")
    else:
        print(f"\n📊 Tracked Targets ({len(rows)}):")
        for url, name, ttype, posts, last in rows:
            print(f"  {name or url} [{ttype}]: {posts} posts (last: {last or 'never'})")
    conn.close()


def show_db_stats():
    conn = sqlite3.connect(str(DB_FILE))
    c = conn.cursor()
    print(f"\n📊 Database Stats")
    print(f"   Targets:  {c.execute('SELECT COUNT(*) FROM targets').fetchone()[0]}")
    print(f"   Posts:    {c.execute('SELECT COUNT(*) FROM posts').fetchone()[0]}")
    print(f"   Replies:  {c.execute('SELECT COUNT(*) FROM tweet_replies').fetchone()[0]}")
    ok = c.execute("SELECT COUNT(*) FROM scrape_log WHERE status='success'").fetchone()[0]
    err = c.execute("SELECT COUNT(*) FROM scrape_log WHERE status='error'").fetchone()[0]
    print(f"   Scrapes:  {ok} success, {err} errors")
    print(f"   Total likes: {c.execute('SELECT SUM(likes) FROM posts').fetchone()[0] or 0}")
    print(f"   Total reposts: {c.execute('SELECT SUM(reposts) FROM posts').fetchone()[0] or 0}")
    conn.close()


def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")


def log_scrape(target_url, started, new_p, total_p, new_r, status="success", error=""):
    conn = sqlite3.connect(str(DB_FILE))
    c = conn.cursor()
    c.execute("""INSERT INTO scrape_log (target_url, started_at, finished_at, posts_new, posts_total, replies_new, status, error)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
              (target_url, started, datetime.now().isoformat(), new_p, total_p, new_r, status, error))
    conn.commit()
    conn.close()


# ── Save to DB ──────────────────────────────────────────────────────

def save_posts(target_url, posts):
    conn = sqlite3.connect(str(DB_FILE))
    c = conn.cursor()

    # Get existing IDs
    c.execute("SELECT post_id FROM posts WHERE target_url = ?", (target_url,))
    known = {r[0] for r in c.fetchall()}

    new_posts = 0
    new_replies = 0

    for post in posts:
        pid = post.get("id", "")
        if not pid or pid in known:
            continue

        new_posts += 1
        c.execute("""
            INSERT OR REPLACE INTO posts
            (post_id, target_url, post_url, author, author_handle, text, timestamp,
             replies, reposts, likes, image_urls, video_url, image_content, scraped_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (pid, target_url, post.get("url", ""), post.get("author", ""),
              post.get("author_handle", ""), post.get("text", ""), post.get("timestamp", ""),
              post.get("replies", 0), post.get("reposts", 0), post.get("likes", 0),
              json.dumps(post.get("image_urls", [])), post.get("video_url", ""),
              json.dumps(post.get("image_content", [])), post.get("scraped_at", "")))

        for reply in post.get("comments", []):
            new_replies += 1
            c.execute("INSERT INTO tweet_replies (post_id, target_url, author, author_handle, text, timestamp, scraped_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                      (pid, target_url, reply.get("author", ""), reply.get("author_handle", ""),
                       reply.get("text", ""), reply.get("timestamp", ""), post.get("scraped_at", "")))

    # Update target stats
    now = datetime.now().isoformat()
    c.execute("""UPDATE targets SET last_scraped = ?,
                total_posts = (SELECT COUNT(*) FROM posts WHERE target_url = ?),
                total_replies = (SELECT COUNT(*) FROM tweet_replies WHERE target_url = ?)
                WHERE target_url = ?""", (now, target_url, target_url, target_url))

    conn.commit()
    conn.close()
    return new_posts, new_replies


# ── Runner ──────────────────────────────────────────────────────────

def run_scraper(target_url):
    venv_python = BASE_DIR / "venv" / "bin" / "python3"
    scraper = BASE_DIR / "scraper.py"
    limit = random.randint(40, 100)

    log(f"  🔧 Scraping {limit} posts...")

    result = subprocess.run(
        [str(venv_python), str(scraper),
         "--url", target_url,
         "--headless",
         "--limit", str(limit),
         "--replies", "--images", "--export", "json"],
        capture_output=True, text=True,
        timeout=900, cwd=str(BASE_DIR),
    )

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
        try:
            data = json.load(f.open())
            if isinstance(data, list):
                return data
        except:
            continue
    return []


# ── Anti-Detection ──────────────────────────────────────────────────

def get_scrapes_today():
    conn = sqlite3.connect(str(DB_FILE))
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM scrape_log WHERE started_at > datetime('now', '-24 hours')")
    count = c.fetchone()[0]
    conn.close()
    return count


def pick_next_target():
    conn = sqlite3.connect(str(DB_FILE))
    c = conn.cursor()

    # Avoid targets scraped in last 6 hours
    c.execute("SELECT target_url FROM scrape_log WHERE started_at > datetime('now', '-6 hours')")
    recent = {r[0] for r in c.fetchall()}

    if recent:
        placeholders = ','.join(['?'] * len(recent))
        c.execute(f"""SELECT target_url, target_name FROM targets
                     WHERE target_url NOT IN ({placeholders})
                     ORDER BY COALESCE(last_scraped, '1970-01-01') ASC
                     LIMIT 5""", list(recent))
    else:
        c.execute("""SELECT target_url, target_name FROM targets
                     ORDER BY COALESCE(last_scraped, '1970-01-01') ASC
                     LIMIT 5""")
    candidates = c.fetchall()
    conn.close()

    if not candidates:
        return None
    return random.choice(candidates)


def should_take_break(session_count):
    return session_count > 0 and session_count % BREAK_EVERY_N == 0


# ── Scheduler ───────────────────────────────────────────────────────

def scrape_one():
    hour = datetime.now().hour
    if QUIET_START <= hour < QUIET_END:
        log(f"😴 Quiet hours ({QUIET_START}-{QUIET_END} AM)")
        return False

    if get_scrapes_today() >= MAX_SCRAPES_PER_DAY:
        log(f"⏸️ Daily limit ({MAX_SCRAPES_PER_DAY})")
        return False

    target = pick_next_target()
    if not target:
        log("⚠️ No targets available")
        return False

    url, name = target
    started = datetime.now().isoformat()
    log(f"📡 [{name or url}] Starting...")

    try:
        posts = run_scraper(url)
        if not posts:
            log(f"  ⚠️ No posts")
            log_scrape(url, started, 0, 0, 0, "no_posts")
            return True

        new_p, new_r = save_posts(url, posts)
        log(f"  ✅ {len(posts)} posts ({new_p} new), {new_r} replies")
        log_scrape(url, started, new_p, len(posts), new_r)
        return True
    except subprocess.TimeoutExpired:
        log(f"  ⏰ Timeout")
        log_scrape(url, started, 0, 0, 0, "error", "Timeout")
        return True
    except Exception as e:
        log(f"  ❌ {e}")
        log_scrape(url, started, 0, 0, 0, "error", str(e)[:500])
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
    parser.add_argument("--add", type=str, metavar="URL", help="Add target (can use multiple times)")
    parser.add_argument("--add-name", type=str, default="", help="Name for --add")
    parser.add_argument("--targets", action="store_true", help="List targets")
    parser.add_argument("--db-stats", action="store_true")
    parser.add_argument("--daemon", action="store_true")
    args = parser.parse_args()

    init_db()

    if args.targets:
        list_targets()
    elif args.add:
        name = args.add_name or args.add.rstrip("/").split("/")[-1]
        ttype = "list" if "/lists/" in args.add else "profile"
        add_target(args.add, name, ttype)
        list_targets()
    elif args.db_stats:
        show_db_stats()
    elif args.daemon:
        run_daemon()
    else:
        scrape_one()


if __name__ == "__main__":
    main()
