#!/bin/bash
# X Scraper — cron wrapper with Discord updates
# Portable: works on WSL (x-crawler) and EC2 (x-scraper) without edits.
BASE="$(dirname "$(readlink -f "$0")")"
cd "$BASE"
WORKSPACE="$(dirname "$BASE")"
export CRAWLER_DB="$WORKSPACE/crawlers/unified.db"
# Host identity — read from crawlers/HOST file so each deployment is self-declaring.
export CRAWLER_HOST="$(cat "$WORKSPACE/crawlers/HOST" 2>/dev/null || echo wsl)"
DISCORD_NOTIFY="$WORKSPACE/scripts/discord_notify.py"

LOG="scheduler.log"

# Run scraper
OUTPUT=$(./venv/bin/python3 run_scheduler.py 2>&1)
echo "$OUTPUT" >> "$LOG"

# Extract stats
POSTS_NEW=$(echo "$OUTPUT" | grep "new," | tail -1 | grep -oP '\d+(?= new)' || echo "0")
REPLIES=$(echo "$OUTPUT" | grep "replies" | tail -1 | grep -oP '\d+(?= replies)' || echo "0")
TARGET=$(echo "$OUTPUT" | grep "📡" | tail -1 | sed 's/.*\[\(.*\)\].*/\1/')
STATUS=$(echo "$OUTPUT" | grep -E "✅|❌|⚠️" | tail -1)

# Get DB stats from unified DB (platform-filtered)
DB_STATS=$(./venv/bin/python3 -c "
import sqlite3, os
c = sqlite3.connect(os.environ['CRAWLER_DB']).cursor()
print(c.execute(\"SELECT COUNT(*) FROM posts WHERE platform='twitter'\").fetchone()[0])
print(c.execute(\"SELECT COALESCE(SUM(thread_count),0) FROM posts WHERE platform='twitter'\").fetchone()[0])
print(c.execute(\"SELECT COUNT(*) FROM scrape_log WHERE status='success' AND platform='twitter'\").fetchone()[0])
" 2>/dev/null)

TOTAL_POSTS=$(echo "$DB_STATS" | sed -n '1p')
TOTAL_REPLIES=$(echo "$DB_STATS" | sed -n '2p')
TOTAL_RUNS=$(echo "$DB_STATS" | sed -n '3p')

# Send Discord notification (only if notifier exists)
if [ -x "$DISCORD_NOTIFY" ] || [ -f "$DISCORD_NOTIFY" ]; then
  python3 "$DISCORD_NOTIFY" "crawler" "🐦 **X Scraper Update**
${STATUS}
**Scraped:** @${TARGET} — ${POSTS_NEW} new posts, ${REPLIES} replies
**Database:** ${TOTAL_POSTS} posts, ${TOTAL_REPLIES} replies total
**Runs:** ${TOTAL_RUNS} successful scrapes" 2>/dev/null
fi
