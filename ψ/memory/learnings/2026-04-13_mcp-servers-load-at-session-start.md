# Lesson: MCP servers load at session start — and "tool missing" is almost always fixable

**Date**: 2026-04-13
**Source**: rrr: x-crawler
**Tags**: #mcp #claude-code #rationalization #infrastructure #anti-patterns

## Two lessons bundled together

### 1. "Blocked because tool X isn't available" is almost always fixable

I wrote "Oracle sync: still blocked — `arra_learn` MCP tool isn't wired into this session" in a retro and moved on without trying to fix it. The user said "fix this" and 15 minutes later it was fixed. The fix was:

- Find upstream repo (`Soul-Brews-Studio/arra-oracle-v2`)
- Read its README
- Copy its one-line install recipe
- Run `claude mcp add`

Total research: 10 min. Total fix: 2 min. The cost of *trying* was barely higher than the cost of *excusing*.

**The anti-rationalization guard in `/rrr` v3.9+ catches this exact pattern** — see the "The API/tool didn't work" row in the excuse table. "Didn't work" is not a diagnosis. If I'm about to write that, I need to have already tried something and have a specific error to point at.

**Application**: when I'm about to label something blocked, stop and ask — "what would it take to try, right now?" If the answer is < 20 min, just try. If the answer is > 20 min, at least document the specific blocker, not the absence.

### 2. MCP servers register at config-write time but load at session start

`claude mcp add` writes to `~/.claude.json` immediately, and `claude mcp list` will report `✓ Connected` right away because it does an on-demand health check. But the MCP client inside a *running* Claude Code session only loads servers once, at the start. Adding one mid-session doesn't expose its tools to you in that session.

```
┌─────────────────────┐     ┌──────────────────────┐
│  claude mcp add     │ ──> │  ~/.claude.json      │ ──> config updated
│                     │     │  (mcpServers entry)  │
└─────────────────────┘     └──────────────────────┘
                                        │
                                        v
                        ┌────────────────────────────┐
                        │  NEXT session start        │ ──> tools loaded
                        │  (cold boot of MCP client) │
                        └────────────────────────────┘
```

**Application**: if a user asks to wire up an MCP tool mid-session, the honest answer is "added, will be available next session". Don't claim you just enabled something for them in real time. If they need it *right now*, you can try `/mcp reload` (does not exist today) or spawn a subprocess that speaks MCP stdio directly — both are more work than just telling them to restart.

## How the two lessons connect

Both are about **not accepting the surface of a situation**. The first lesson: don't accept "tool missing" as a terminal state; go find the tool. The second: don't accept "I added it" as meaning "it's active"; understand the lifecycle.

They're the same muscle — asking "is this actually what's happening?" — applied to two different layers (availability vs. activation).

## Related

- The earlier retro's lesson (`2026-04-13_verify-content-not-status-lines.md`) is also a member of this family: don't trust a "50/50 resolved" summary when the 50 values are all "Chats". All three are instances of "check under the surface before moving on".
- Upstream repo: https://github.com/Soul-Brews-Studio/arra-oracle-v2
- Install recipe: `claude mcp add -s user arra-oracle-v2 -- bunx --bun arra-oracle-v2@github:Soul-Brews-Studio/arra-oracle-v2#main`
