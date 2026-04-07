#!/usr/bin/env python3
"""
X (Twitter) Scraper — Anti-Detection Deep Extraction

Scrapes posts, replies, images, and video URLs from X profiles/lists.
Uses Playwright with stealth JS + persistent session.

Usage:
  source venv/bin/activate
  python scraper.py --login                              # First time
  python scraper.py --url <profile_or_list> --limit 50
  python scraper.py --url <profile> --replies --images --limit 100
"""

import asyncio
import json
import random
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from playwright.async_api import async_playwright

DATA_DIR = Path(__file__).parent / "data"
SESSION_FILE = Path(__file__).parent / "session.json"

STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
Object.defineProperty(navigator, 'plugins', {
    get: () => [1, 2, 3, 4, 5].map(() => ({ name: 'Chrome PDF Plugin' }))
});
Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
window.chrome = { runtime: {} };
"""


async def random_delay(lo=1.5, hi=4.0):
    await asyncio.sleep(random.uniform(lo, hi))


async def human_scroll(page):
    d = random.randint(300, 800)
    await page.evaluate(f"window.scrollBy(0, {d})")
    await random_delay(0.5, 1.5)
    await page.mouse.move(random.randint(100, 800), random.randint(100, 600),
                          steps=random.randint(5, 15))
    await random_delay(0.2, 0.5)


async def safe_text(el):
    try:
        return (await el.inner_text()).strip()
    except:
        return ""


async def safe_attr(el, attr):
    try:
        return await el.get_attribute(attr) or ""
    except:
        return ""


# X-specific noise
X_NOISE = {
    'reply', 'repost', 'like', 'bookmark', 'share', 'view',
    'replies', 'reposts', 'likes', 'bookmarks', 'views',
    'read', 'more', 'translate', 'copy link', 'embed post',
    'who can reply', 'follow', 'following', 'subscribe',
    'promoted', 'pinned', 'joined', 'show replies',
    'see new posts', 'see more', 'show more',
    'edited', 'you reposted', 'you liked', 'you bookmarked',
}

def is_x_noise(text):
    low = text.strip().lower()
    if not low or len(low) <= 1:
        return True
    if low in X_NOISE:
        return True
    # Timestamps like "2h", "5m", "Dec 15", "1:23 PM"
    if re.match(r'^\d+[hms]?$', low):
        return True
    if re.match(r'^\d{1,2}:\d{2}\s*[ap]m$', low):
        return True
    if re.match(r'^[a-z]{3}\s+\d{1,2}$', low):
        return True
    # Pure numbers (view counts, etc)
    if re.match(r'^[\d,.]+k?m?$', low):
        return True
    # "·" separator
    if low == '·' or low == '···' or low == '⋯':
        return True
    return False


# ── Browser ─────────────────────────────────────────────────────────

async def create_browser(headless=False):
    pw = await async_playwright().start()
    browser = await pw.chromium.launch(
        headless=headless,
        args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
    )
    ctx = await browser.new_context(
        viewport={"width": 1280, "height": 800},
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36",
        locale="en-US", timezone_id="Asia/Bangkok",
        extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
        accept_downloads=True,
    )
    if SESSION_FILE.exists():
        await ctx.add_cookies(json.loads(SESSION_FILE.read_text()))
        print("✅ Loaded saved session")
    page = await ctx.new_page()
    await page.add_init_script(STEALTH_JS)
    return pw, browser, ctx, page


async def save_session(ctx):
    cookies = await ctx.cookies()
    SESSION_FILE.write_text(json.dumps(cookies, indent=2))
    print(f"✅ Session saved ({len(cookies)} cookies)")


async def do_login(page):
    print("\n🔐 Opening X login...")
    await page.goto("https://x.com/login")
    await random_delay(3, 5)
    print("⏳ Log in manually. Press ENTER when done.")
    await asyncio.get_event_loop().run_in_executor(None, input)
    await page.goto("https://x.com/home")
    await random_delay(2, 3)
    if "login" in page.url.lower():
        print("❌ Login failed.")
        return False
    print("✅ Login successful!")
    return True


# ── Scraping ────────────────────────────────────────────────────────

async def scrape_target(page, target_url, limit=50, do_replies=False, do_images=False):
    """Scrape posts from a profile, list, or search page."""
    print(f"\n📡 Navigating to: {target_url}")
    await page.goto(target_url)
    await random_delay(4, 7)

    try:
        await page.wait_for_selector('article', timeout=15000)
    except:
        print("⚠️ Posts didn't load — check URL or login")
        return []

    posts = []
    seen_ids = set()
    stale = 0

    print(f"🎯 Target: {limit} posts\n")

    while len(posts) < limit:
        articles = await page.query_selector_all('article')
        new = 0

        for article in articles:
            if len(posts) >= limit:
                break

            # Get tweet link for ID + permalink
            tweet_id = ""
            tweet_url = ""
            try:
                # Time link is the most reliable: a[href*="/status/"] inside time element
                time_links = await article.query_selector_all('time a[href*="/status/"], a[href*="/status/"]')
                for tl in time_links:
                    href = await safe_attr(tl, "href")
                    if href:
                        m = re.search(r'/status/(\d+)', href)
                        if m:
                            tweet_id = m.group(1)
                            tweet_url = f"https://x.com{href}" if href.startswith('/') else href
                            break
            except:
                pass

            if not tweet_id or tweet_id in seen_ids:
                continue

            seen_ids.add(tweet_id)
            new += 1

            # ── Author ──
            author = ""
            author_handle = ""
            try:
                # X has user info in [data-testid="User-Name"] or first link
                user_el = await article.query_selector('[data-testid="User-Name"]')
                if user_el:
                    links = await user_el.query_selector_all('a')
                    for link in links:
                        href = await safe_attr(link, "href")
                        txt = await safe_text(link)
                        if href and '/' in href:
                            author_handle = href.rstrip('/').split('/')[-1]
                        if txt and not is_x_noise(txt):
                            author = txt
                            break
            except:
                pass

            # ── Timestamp ──
            timestamp = ""
            try:
                time_el = await article.query_selector('time')
                if time_el:
                    timestamp = await safe_attr(time_el, "datetime") or await safe_text(time_el)
            except:
                pass

            # ── Full text ──
            text = ""
            try:
                # Main tweet text
                tweet_el = await article.query_selector('[data-testid="tweetText"]')
                if tweet_el:
                    text = await safe_text(tweet_el)
                else:
                    # Fallback: get all text from article
                    all_text = await safe_text(article)
                    lines = [l.strip() for l in all_text.split('\n') if l.strip() and not is_x_noise(l)]
                    text = '\n'.join(lines)
            except:
                pass

            # ── Metrics ──
            replies = 0
            reposts = 0
            likes = 0
            views = 0
            try:
                # X uses data-testid for metric buttons
                for label, testid in [('replies', 'reply'), ('reposts', 'retweet'),
                                       ('likes', 'like'), ('views', 'bookmark')]:
                    btn = await article.query_selector(f'[data-testid="{testid}"]')
                    if btn:
                        txt = await safe_text(btn)
                        nums = re.findall(r'[\d,.]+[kKmM]?', txt)
                        if nums:
                            val = nums[0]
                            mult = 1
                            if val.endswith('k') or val.endswith('K'):
                                mult = 1000
                            elif val.endswith('m') or val.endswith('M'):
                                mult = 1000000
                            num = float(re.sub(r'[^0-9.]', '', val)) * mult
                            if label == 'replies':
                                replies = int(num)
                            elif label == 'reposts':
                                reposts = int(num)
                            elif label == 'likes':
                                likes = int(num)
            except:
                pass

            # ── Image URLs ──
            image_urls = []
            if do_images:
                try:
                    imgs = await article.query_selector_all('img[src*="pbs.twimg.com"]')
                    for img in imgs:
                        src = await safe_attr(img, "src")
                        if src and "pbs.twimg.com" in src:
                            # Get full resolution
                            src = src.split('?')[0] + '?format=jpg&name=large'
                            image_urls.append(src)
                except:
                    pass

            # ── Video ──
            video_url = ""
            try:
                videos = await article.query_selector_all('video')
                for v in videos:
                    src = await safe_attr(v, "src")
                    poster = await safe_attr(v, "poster")
                    if src:
                        video_url = src
                        break
                    elif poster and "pbs.twimg.com" in poster:
                        video_url = poster
                if not video_url:
                    # Video card link
                    vlink = await article.query_selector('a[href*="/video/"]')
                    if vlink:
                        video_url = await safe_attr(vlink, "href")
            except:
                pass

            # ── Image content analysis ──
            image_content = []
            if do_images and image_urls:
                image_content = analyze_image_urls(image_urls[:4])

            # ── Replies (if requested, open tweet page) ──
            comments = []
            if do_replies and tweet_url:
                comments = await scrape_replies(page, tweet_url)

            posts.append({
                "id": tweet_id,
                "url": tweet_url,
                "author": author,
                "author_handle": author_handle,
                "text": text,
                "timestamp": timestamp,
                "replies": replies,
                "reposts": reposts,
                "likes": likes,
                "image_urls": image_urls,
                "video_url": video_url,
                "image_content": image_content,
                "comments": comments,
                "scraped_at": datetime.now().isoformat(),
            })

            n = len(posts)
            preview = (text or '')[:70].replace('\n', ' ')
            ic = f" [{len(image_content)} img]" if image_content else ""
            cm = f" [{len(comments)} replies]" if comments else ""
            print(f"  [{n}/{limit}] @{author_handle or '?'}{ic}{cm}: {preview}")

            await random_delay(0.3, 0.8)

        await human_scroll(page)

        if new == 0:
            stale += 1
            if stale > 12:
                print(f"\n🛑 No new posts after 12 scrolls.")
                break
        else:
            stale = 0

    print(f"\n✅ Collected {len(posts)} posts")
    return posts


async def scrape_replies(page, tweet_url, max_replies=30):
    """Open a tweet and scrape replies."""
    replies = []
    try:
        await page.goto(tweet_url)
        await random_delay(2, 4)

        # Scroll to load replies
        for _ in range(3):
            await human_scroll(page)
            await random_delay(1, 2)

        articles = await page.query_selector_all('article')
        for article in articles[:max_replies]:
            try:
                # Get reply author + text
                user_el = await article.query_selector('[data-testid="User-Name"]')
                author = ""
                handle = ""
                if user_el:
                    links = await user_el.query_selector_all('a')
                    for link in links:
                        href = await safe_attr(link, "href")
                        txt = await safe_text(link)
                        if href:
                            handle = href.rstrip('/').split('/')[-1]
                        if txt and not is_x_noise(txt):
                            author = txt
                            break

                tweet_el = await article.query_selector('[data-testid="tweetText"]')
                text = await safe_text(tweet_el) if tweet_el else ""

                if text:
                    replies.append({
                        "author": author,
                        "author_handle": handle,
                        "text": text,
                    })
            except:
                continue
    except:
        pass
    return replies


# ── Vision Analysis ─────────────────────────────────────────────────

def analyze_image_urls(urls):
    analyzer = Path(__file__).parent / "vision_analyze.py"
    if not analyzer.exists():
        return ["[analyzer not found]"]
    try:
        tmp = DATA_DIR / "_tmp_urls.json"
        tmp.write_text(json.dumps(urls))
        result = subprocess.run(
            [sys.executable, str(analyzer), str(tmp)],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode == 0:
            return json.loads(result.stdout.strip())
        return [f"[error: {result.stderr[:200]}]"]
    except Exception as e:
        return [f"[error: {e}]"]


# ── Export ──────────────────────────────────────────────────────────

def export_data(posts, target_name, target_dir):
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    target_dir.mkdir(parents=True, exist_ok=True)

    fp = target_dir / f"{ts}.json"
    with open(fp, "w", encoding="utf-8") as f:
        json.dump(posts, f, ensure_ascii=False, indent=2)
    print(f"📁 JSON: {fp}")

    fp_md = target_dir / f"{ts}.md"
    with open(fp_md, "w", encoding="utf-8") as f:
        f.write(f"# {target_name} — {ts}\n\n")
        for i, p in enumerate(posts, 1):
            f.write(f"## Post {i}\n")
            f.write(f"**Author:** {p.get('author','?')} (@{p.get('author_handle','?')}) | "
                    f"**Date:** {p.get('timestamp','?')}\n")
            f.write(f"**Replies:** {p.get('replies',0)} | "
                    f"**Reposts:** {p.get('reposts',0)} | "
                    f"**Likes:** {p.get('likes',0)}\n")
            f.write(f"**URL:** {p.get('url','')}\n\n")
            if p.get("text"):
                f.write(f"{p['text']}\n\n")
            if p.get("image_content"):
                f.write("### Image Content\n")
                for ic in p["image_content"]:
                    f.write(f"- {ic}\n")
                f.write("\n")
            if p.get("video_url"):
                f.write(f"### Video\n`{p['video_url']}`\n\n")
            if p.get("comments"):
                f.write(f"### Replies ({len(p['comments'])})\n")
                for c in p["comments"]:
                    f.write(f"- @{c.get('author_handle','?')}: {c['text']}\n")
                f.write("\n")
            f.write("---\n\n")
    print(f"📁 Markdown: {fp_md}")

    # Summary
    summary = {
        "scraped_at": ts,
        "total_posts": len(posts),
        "total_replies_scraped": sum(len(p.get("comments", [])) for p in posts),
        "total_images": sum(len(p.get("image_urls", [])) for p in posts),
        "total_image_analyses": sum(len(p.get("image_content", [])) for p in posts),
        "total_likes": sum(p.get("likes", 0) for p in posts),
        "total_reposts": sum(p.get("reposts", 0) for p in posts),
    }
    sp = target_dir / f"{ts}_summary.json"
    sp.write_text(json.dumps(summary, indent=2))
    print(f"📁 Summary: {sp}")


# ── Main ────────────────────────────────────────────────────────────

async def main():
    import argparse
    parser = argparse.ArgumentParser(description="X (Twitter) Scraper")
    parser.add_argument("--login", action="store_true")
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--url", type=str)
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--replies", action="store_true", help="Scrape replies on each post")
    parser.add_argument("--images", action="store_true", help="Analyze images")
    parser.add_argument("--export", choices=["json", "markdown", "both"], default="both")
    args = parser.parse_args()

    pw, browser, ctx, page = await create_browser(headless=args.headless)

    try:
        if args.login:
            if await do_login(page):
                await save_session(ctx)
            return

        if not args.url:
            print("❌ Provide --url or --login first")
            print("   python scraper.py --login")
            print("   python scraper.py --url 'https://x.com/elonmusk' --limit 50")
            print("   python scraper.py --url 'https://x.com/i/lists/123456' --replies --images --limit 30")
            return

        posts = await scrape_target(page, args.url, limit=args.limit,
                                     do_replies=args.replies, do_images=args.images)

        if posts:
            # Extract target name from URL
            target = args.url.rstrip("/").split("/")[-1]
            target_dir = DATA_DIR / target

            if args.export in ("json", "both"):
                export_data(posts, target, target_dir)
            if args.export in ("markdown", "both") and args.export != "json":
                pass  # already done in export_data

            await save_session(ctx)
            print(f"\n🎉 {len(posts)} posts → {target_dir}/")

    except KeyboardInterrupt:
        print("\n⚠️ Interrupted")
    except Exception as e:
        print(f"\n❌ {e}")
        import traceback
        traceback.print_exc()
    finally:
        await ctx.close()
        await browser.close()
        await pw.stop()


if __name__ == "__main__":
    asyncio.run(main())
