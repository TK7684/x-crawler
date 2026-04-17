# Handoff: Unified Crawler DB Rollout (WSL + EC2)

**Date**: 2026-04-13 11:13 GMT+7
**Context**: ~85% (after a ~5h session — one big plan → implement → deploy → verify cycle)
**Next focus**: Fix `resolve_names.py` heuristic; decide on `.bak` cleanup + systemd removal

## What We Did

- **Unified both crawlers onto a single SQLite DB** via a shared `crawler_db.py` module (4-table polymorphic schema: `sources` / `posts` / `threads` / `scrape_log`, JSON `engagement`, WAL). Migrated 90 sources + 1219 posts + 1269 threads across WSL and EC2. Legacy DBs archived as `.bak`.
- **Replicated the whole thing on EC2** (`18.142.43.20`). Discovered mid-session that **both** crawlers run on EC2, not just fb-crawler — Oracle memory was incomplete. Plus `clean_data.py` hourly pipeline, which turned out to read JSON exports (unaffected by the DB refactor). Patched `hourly_update.sh` to use unified DB. Disabled `fb-crawler.service` / `x-scraper.service` (they were `Restart=always`-respawning killed daemons). Cron now drives scheduling exclusively.
- **Target split 46/44 across WSL/EC2** via `sources.metadata.host` + `CRAWLER_HOST` env var read from per-host `crawlers/HOST` file. Deterministic round-robin by sorted `external_id` → both hosts produce identical assignments independently.
- **Portable `cron_run.sh`** — same file works on WSL and EC2 without edits, via `$(dirname "$(readlink -f "$0")")` path resolution. Fixes a pre-existing `cd /home/tk578/.../x-scraper` typo.
- **EC2→WSL rsync cron** (`sync_from_ec2.sh`, every 2h offset 15min) pulls EC2's `unified.db` to WSL's `unified-ec2.db` for combined analytics.
- **Live cron verified post-refactor** — WSL 10:00 tick scraped `0xairdropfarmer` (host=wsl ✓), 10:28 manual trigger scraped `EthereumThaila1` (host=wsl ✓). FB cron at 09:00 wrote 11 new posts + 8 new replies successfully.
- **Committed and pushed** to both GitHub repos: `TK7684/x-crawler 59736e8`, `TK7684/fb-crawler 31b3d11` — both titled `refactor: unify DB layer via shared crawler_db.py`.
- **Claude Code auto-memory** saved: `ec2_crawler_infra.md` (reference), `unified_crawler_db.md` (project).

## Pending

- [ ] **`resolve_names.py` is broken** — attempted run produced 50/50 "Chats" (FB chat sidebar leaking into DOM scrape fallback chain). Rolled back. Script needs rewrite — try `og:title` only, or the `/about` page, or Graph API. Add content sanity check: fail if all resolved values are identical.
- [ ] Delete legacy `.bak` files on both hosts after ~1 week of confidence: `x_scraper.db.bak`, `fb_groups.db.bak`.
- [ ] Decide systemd service fate on EC2: delete unit files (`rm /etc/systemd/system/{fb-crawler,x-scraper}.service`) or keep them disabled as manual fallback.
- [ ] Rename EC2 project dirs: `x-scraper` → `x-crawler`, `fb-group-scraper` → `fb-crawler`. Blocks `git pull`-based deploys otherwise.
- [ ] Delete empty shell DBs in repo dirs: `x-crawler/scraper.db` + `x_profiles.db`, `fb-crawler/scraper.db`. All zero-byte leftovers from scraper.py error paths, already in `.gitignore`.

## Next Session

- [ ] Rewrite `resolve_names.py` with a defensible extraction strategy + sanity check
- [ ] Inspect first EC2 cron tick post-deploy — check `/home/ec2-user/scripts/hourly_update.sh` output arrives at Discord correctly, and that the 12:00 UTC x-scraper cron picks an ec2-assigned target
- [ ] Consider adding a tiny `combined.db` view on WSL that `ATTACH`es both `unified.db` and `unified-ec2.db` for one-query cross-host analytics
- [ ] Lower-priority: add `resolve_names` content sanity check as a general pattern — extend `simplify` skill or add a check helper

## Key Files

**Shared (created this session):**
- `~/.openclaw/workspace/crawlers/crawler_db.py` — 590 lines, 13 public functions
- `~/.openclaw/workspace/crawlers/migrate.py` — one-shot migration with `--x-db`/`--fb-db` overrides
- `~/.openclaw/workspace/crawlers/assign_hosts.py` — 50/50 round-robin
- `~/.openclaw/workspace/crawlers/sync_from_ec2.sh` — rsync pull
- `~/.openclaw/workspace/crawlers/HOST` — host identity marker
- `~/.openclaw/workspace/crawlers/unified.db` — live production DB (WSL)
- `~/.openclaw/workspace/crawlers/unified-ec2.db` — synced copy of EC2 DB

**Modified and committed:**
- `~/.openclaw/workspace/x-crawler/run_scheduler.py`
- `~/.openclaw/workspace/x-crawler/cron_run.sh`
- `~/.openclaw/workspace/fb-crawler/run_scheduler.py`
- `~/.openclaw/workspace/fb-crawler/resolve_names.py`
- `~/.openclaw/workspace/fb-crawler/cron_run.sh`

**EC2:**
- `ec2-user@18.142.43.20:/home/ec2-user/crawlers/` — mirror of shared dir
- `ec2-user@18.142.43.20:/home/ec2-user/scripts/hourly_update.sh` — patched
- SSH key: `C:\Users\ttapk\Downloads\e2c-crawler.pem` (copy to WSL `~/.ssh/e2c-crawler.pem` with 600 perms)

**Context for next session:**
- `~/.claude/projects/.../memory/ec2_crawler_infra.md`
- `~/.claude/projects/.../memory/unified_crawler_db.md`
- `ψ/memory/retrospectives/2026-04/13/11.02_unified-crawler-db-wsl-ec2-rollout.md`
- `ψ/memory/learnings/2026-04-13_verify-content-not-status-lines.md`

---

## Post-handoff update — 12:08 GMT+7: Arra Oracle MCP wired

After the initial handoff I addressed the "Oracle sync blocked" caveat. `arra-oracle-v2` MCP server is now registered at **user scope** in `~/.claude.json` and reporting `✓ Connected` via `claude mcp list`. Loads via `bunx --bun arra-oracle-v2@github:Soul-Brews-Studio/arra-oracle-v2#main`.

**However**: MCP servers load at session start, so `arra_learn` tool isn't available in the current session. Next Claude Code session will expose it automatically.

**For the next session**: run `/rrr` (or call `arra_learn` directly) to ingest the two disk-resident lessons:
- `ψ/memory/learnings/2026-04-13_verify-content-not-status-lines.md`
- `ψ/memory/learnings/2026-04-13_mcp-servers-load-at-session-start.md` (new)

**If upstream breaks** (it's pinned to `#main`): `claude mcp remove arra-oracle-v2 -s user && claude mcp add -s user arra-oracle-v2 -- bunx --bun arra-oracle-v2@github:Soul-Brews-Studio/arra-oracle-v2#<sha-or-tag>`

Sub-task retro: `ψ/memory/retrospectives/2026-04/13/12.08_wire-arra-oracle-mcp.md`
