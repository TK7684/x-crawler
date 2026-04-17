# Lesson: Verify output content, not just status lines

**Date**: 2026-04-13
**Source**: rrr: x-crawler
**Tags**: #verification #automation #quality-checks #anti-patterns

## Pattern

A script's "success count" tells you how many rows it *wrote*, not whether the values are *useful*. Before reporting success, spot-check the actual content — especially when the output is user-facing (Discord names, labels, URLs, display text).

## Evidence

Ran `resolve_names.py` on 50 Facebook groups. Script reported:

```
✅ Resolved: 50/50 | Failed: 0
```

Technically true: 50 rows updated, zero exceptions. But every single resolved value was the literal string `"Chats"` — the DOM fallback was picking up FB's chat sidebar widget when the user wasn't a full member of the group (which was the case for most of them). I almost reported this as success to the user; only noticed when I scrolled up in the log and saw the monotonous pattern.

The fix cost: `mv fb_groups.db.bak → restored from`, one Python UPDATE loop, done in 30 seconds. But if it had landed unnoticed, the next Discord notification would have been a wall of "Chats Chats Chats" and someone would have had to debug it cold.

Related near-miss earlier in the same session: I grep'd `x-crawler/` for `ec2` and got 3 file matches. I didn't open any of them — I assumed they were false positives matching the substring "x-scraper". Confidently told the user "x-crawler has no EC2 references." Two minutes into a later SSH session I found an entire production deployment at `/home/ec2-user/x-scraper/`.

## Application

Two concrete habits:

1. **Negative claims require positive verification.** "X has no Y" is a load-bearing claim. If you're about to say it, open at least one of the matches first. If there are zero matches, that's already verification. But if grep *does* return hits, you can't dismiss them without looking.

2. **Scripts that do bulk updates should sanity-check their own output.** For `resolve_names.py` specifically: if all resolved values are identical after the run, fail the whole batch. More generally, a one-line sanity check costs nothing and catches entire categories of silent bugs. The `simplify` skill (review changed code for reuse, quality, and efficiency) is exactly the right hammer for this nail.

3. **When reviewing a script's log output, look at *content*, not just the summary line.** "50/50" and "50 Chats" both start with 50.

## Meta

This lesson has a cousin in the Oracle memory already: `2026-04-10_ec2-infra-ask-first.md` — "Always ask for SSH credentials rather than guessing; implicit infra knowledge is the biggest time sink." Both are about not trusting your own confident assumptions when the cost of verification is low. Different flavors of the same failure mode: assuming structure (ec2-infra-ask-first) vs. assuming correctness (this one).
