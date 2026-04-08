#!/bin/bash
# X Scraper — cron wrapper with Discord updates
export DISPLAY=:99
cd /home/ec2-user/x-scraper

LOG="scheduler.log"

# Run scraper
OUTPUT=$(./venv/bin/python3 run_scheduler.py 2>&1)
echo "$OUTPUT" >> "$LOG"

# Extract stats
POSTS_NEW=$(echo "$OUTPUT" | grep "new," | tail -1 | grep -oP "\d+(?= new)" || echo "0")
REPLIES=$(echo "$OUTPUT" | grep "replies" | tail -1 | grep -oP "\d+(?= replies)" || echo "0")
TARGET=$(echo "$OUTPUT" | grep "📡" | tail -1 | sed "s/.*\[\(.*\)\].*/\1/")
STATUS=$(echo "$OUTPUT" | grep -E "✅|❌|⚠️" | tail -1)

# Get DB stats
DB_STATS=$(./venv/bin/python3 -c "
import sqlite3
c=sqlite3.connect('x_scraper.db').cursor()
print(f'{c.execute(\"SELECT COUNT(*) FROM posts\").fetchone()[0]}')
print(f'{c.execute(\"SELECT COUNT(*) FROM tweet_replies\").fetchone()[0]}')
print(f'{c.execute(\"SELECT COUNT(*) FROM scrape_log WHERE status=\\\"success\\\"\").fetchone()[0]}')
" 2>/dev/null)

TOTAL_POSTS=$(echo "$DB_STATS" | sed -n "1p")
TOTAL_REPLIES=$(echo "$DB_STATS" | sed -n "2p")
TOTAL_RUNS=$(echo "$DB_STATS" | sed -n "3p")

# Send Discord notification
python3 /home/ec2-user/scripts/discord_notify.py "facebook-group-scrape" "🐦 **X Scraper Update**
${STATUS}
**Scraped:** @${TARGET} — ${POSTS_NEW} new posts, ${REPLIES} replies
**Database:** ${TOTAL_POSTS} posts, ${TOTAL_REPLIES} replies total
**Runs:** ${TOTAL_RUNS} successful scrapes" 2>/dev/null
