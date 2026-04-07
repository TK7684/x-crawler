# X (Twitter) Scraper

Anti-detection X/Twitter scraper with Playwright + SQLite + scheduler.

## Setup

```bash
cd ~/.openclaw/workspace/x-scraper
source venv/bin/activate
```

## First Run — Login

```bash
python scraper.py --login
```

## Scrape

```bash
# Profile tweets
python scraper.py --url "https://x.com/elonmusk" --limit 50

# With replies and image analysis
python scraper.py --url "https://x.com/elonmusk" --replies --images --limit 30

# List
python scraper.py --url "https://x.com/i/lists/123456" --limit 100
```

## Scheduler

```bash
# Initialize DB
./venv/bin/python3 run_scheduler.py --init

# Add targets to track
./venv/bin/python3 run_scheduler.py --add "https://x.com/elonmusk" --add-name "Elon Musk"
./venv/bin/python3 run_scheduler.py --add "https://x.com/i/lists/123456" --add-name "AI List"

# List all targets
./venv/bin/python3 run_scheduler.py --targets

# Start daemon
systemctl --user start x-scraper

# Stop daemon
systemctl --user stop x-scraper

# Check stats
./venv/bin/python3 run_scheduler.py --db-stats
```

## Anti-Detection

- **NO headless** — real browser window (harder to detect)
- **Max 8 scrapes/day** — mimics human usage
- **Random 30-180 min** intervals between scrapes
- **Long breaks** (1-3 hr) every 3 scrapes
- **Quiet hours** 2-5 AM
- **6-hour cooldown** per target
- **Random post limit** (20-60) per scrape
- **Stealth JS** — removes automation fingerprints

## Output

- `data/{target}/` — per-target directories
- JSON + Markdown + summary per scrape
- SQLite DB (`x_scraper.db`) with posts, replies, metrics
