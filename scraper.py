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
import os
import random
import re
import subprocess
import sys
import tempfile
import urllib.request
from datetime import datetime
from pathlib import Path

sys.path.insert(0, "/home/tk578/crawlers")
from vision_analyze import analyze_image as zai_analyze_image

from playwright.async_api import async_playwright

DATA_DIR = Path(__file__).parent / "data"
SESSION_INDEX = int(os.environ.get("X_SESSION_INDEX", "0"))
SESSION_FILE = Path(__file__).parent / f"session_{SESSION_INDEX}.json"


class RateLimitError(Exception):
    """Raised when X returns 429/403 or a rate-limit page. Recoverable —
    daemon should skip this target and let adaptive_cooldown extend backoff."""
    pass

STEALTH_JS = """
// Core: hide webdriver
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });

// Plugins
Object.defineProperty(navigator, 'plugins', {
    get: () => [1, 2, 3, 4, 5].map(() => ({ name: 'Chrome PDF Plugin' }))
});

// Languages
Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en', 'th'] });

// Chrome runtime
window.chrome = { runtime: {}, loadTimes: function(){}, csi: function(){} };

// Permissions
const oq = window.navigator.permissions.query;
window.navigator.permissions.query = (p) =>
    p.name === 'notifications'
        ? Promise.resolve({ state: Notification.permission })
        : oq(p);

// WebGL
const gp = WebGLRenderingContext.prototype.getParameter;
WebGLRenderingContext.prototype.getParameter = function(param) {
    if (param === 37445) return 'Google Inc. (NVIDIA)';
    if (param === 37446) return 'ANGLE (NVIDIA, NVIDIA GeForce RTX 5060 Ti, OpenGL 4.6)';
    return gp.call(this, param);
};
const gp2 = WebGL2RenderingContext.prototype.getParameter;
WebGL2RenderingContext.prototype.getParameter = function(param) {
    if (param === 37445) return 'Google Inc. (NVIDIA)';
    if (param === 37446) return 'ANGLE (NVIDIA, NVIDIA GeForce RTX 5060 Ti, OpenGL 4.6)';
    return gp2.call(this, param);
};

// Hardware concurrency (real CPU)
Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 8 });

// Platform
Object.defineProperty(navigator, 'platform', { get: () => 'Win32' });

// Vendor
Object.defineProperty(navigator, 'vendor', { get: () => 'Google Inc.' });

// Connection (realistic)
Object.defineProperty(navigator, 'connection', {
    get: () => ({ effectiveType: '4g', rtt: 50, downlink: 10, saveData: false })
});

// Media codecs
Object.defineProperty(navigator, 'mediaCapabilities', {
    get: () => ({
        decodingInfo: () => Promise.resolve({ supported: true, powerEfficient: true, smooth: true })
    })
});

// Hide automation flags
delete navigator.__proto__.webdriver;

// Canvas fingerprint noise
const origToDataURL = HTMLCanvasElement.prototype.toDataURL;
HTMLCanvasElement.prototype.toDataURL = function(type) {
    if (type === 'image/png') {
        const ctx = this.getContext('2d');
        if (ctx) {
            const imageData = ctx.getImageData(0, 0, this.width, this.height);
            for (let i = 0; i < imageData.data.length; i += 4) {
                imageData.data[i] ^= 1; // Tiny noise
            }
            ctx.putImageData(imageData, 0, 0);
        }
    }
    return origToDataURL.apply(this, arguments);
};

// Iframe contentWindow
const origContentWindow = Object.getOwnPropertyDescriptor(HTMLIFrameElement.prototype, 'contentWindow');
if (origContentWindow) {
    Object.defineProperty(HTMLIFrameElement.prototype, 'contentWindow', {
        get: function() { return null; }
    });
}
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
    args = [
        "--disable-blink-features=AutomationControlled",
        "--no-sandbox",
        "--disable-dev-shm-usage",
        "--disable-infobars",
        "--disable-extensions",
        "--disable-gpu",
        "--window-size=1280,800",
        "--disable-background-timer-throttling",
        "--no-first-run",
        "--no-default-browser-check",
    ]
    if headless:
        pass

    browser = await pw.chromium.launch(
        headless=headless,
        args=args,
    )
    context_args = dict(
        viewport={"width": 1280, "height": 800},
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36",
        locale="en-US", timezone_id="Asia/Bangkok",
        extra_http_headers={
            "Accept-Language": "en-US,en;q=0.9,th;q=0.8",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
        accept_downloads=True,
        bypass_csp=True,
        java_script_enabled=True,
    )
    proxy_url = os.environ.get("X_PROXY")
    if proxy_url:
        context_args["proxy"] = {"server": proxy_url}
    ctx = await browser.new_context(**context_args)
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

async def scrape_target(page, target_url, limit=50, do_replies=True, do_images=True,
                        known_ids=None, auto_comment=False, max_auto_comments=5):
    """Scrape posts from a profile, list, or search page.
    If known_ids is provided, stops early after consecutive known posts."""
    print(f"\n📡 Navigating to: {target_url}")
    response = await page.goto(target_url)
    await random_delay(4, 7)

    # ── Rate-limit detection ────────────────────────────────────────
    # X may respond with HTTP 429 / 403 or render a "rate limit" page
    # without a non-200 status. Detect both — log clearly, skip target,
    # raise recoverable RateLimitError so adaptive_cooldown extends backoff.
    if response is not None and response.status in (429, 403):
        print(f"\n🚨 Rate limit detected: HTTP {response.status} on {target_url}")
        raise RateLimitError(f"HTTP {response.status} from {target_url}")

    try:
        _body = await page.evaluate("() => (document.body && document.body.innerText) || ''")
    except Exception:
        _body = ""
    _body_lo = _body[:2000].lower()
    for _marker in ("rate limit exceeded", "try again later", "too many requests"):
        if _marker in _body_lo:
            print(f"\n🚨 Rate limit detected: '{_marker}' in page body of {target_url}")
            raise RateLimitError(f"Page text '{_marker}' from {target_url}")

    try:
        await page.wait_for_selector('article', timeout=15000)
    except:
        print("⚠️ Posts didn't load — check URL or login")
        return []

    known_ids = known_ids or set()
    posts = []
    seen_ids = set()
    stale = 0
    consecutive_known = 0
    auto_reply_count = 0

    if auto_comment:
        print(f"🤖 Auto-comment enabled (max {max_auto_comments})")

    print(f"🎯 Target: {limit} posts ({len(known_ids)} already known)\n")

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

            # Early exit on consecutive known posts
            if tweet_id in known_ids:
                consecutive_known += 1
                if consecutive_known >= 10:
                    print(f"\n⏩ 10 consecutive known tweets — stopping early ({len(posts)} new collected)")
                    return posts
                continue
            consecutive_known = 0
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
            timestamp_raw = ""
            try:
                time_el = await article.query_selector('time')
                if time_el:
                    timestamp = await safe_attr(time_el, "datetime") or ""
                    timestamp_raw = await safe_text(time_el) or ""
                    if not timestamp:
                        timestamp = timestamp_raw
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

            # Expand truncated tweets
            if text and (text.rstrip().endswith("…") or text.rstrip().endswith("...") or len(text) < 200):
                full_text = await get_full_tweet_text(page, tweet_url)
                if full_text and len(full_text) > len(text):
                    text = full_text

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

            # ── Image content analysis (inline OCR) ──
            image_content = []
            if do_images and image_urls:
                for img_url in image_urls[:3]:
                    result = analyze_single_x_image(img_url)
                    if result:
                        image_content.append(result)

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
                "timestamp_raw": timestamp_raw,
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

            # ── Auto-comment engagement ──
            if auto_comment and auto_reply_count < max_auto_comments:
                trigger = detect_engagement_trigger(text)
                if trigger and tweet_url:
                    print(f"  🤖 Engagement trigger detected: '{trigger}' — auto-replying...")
                    success = await auto_engage_reply(page, tweet_url, trigger)
                    if success:
                        auto_reply_count += 1
                        print(f"  ✅ Auto-reply #{auto_reply_count}/{max_auto_comments} sent")
                    else:
                        print(f"  ❌ Auto-reply failed")
                    # Random delay between auto-replies (8-20s)
                    await asyncio.sleep(random.uniform(8, 20))

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
    if auto_comment:
        print(f"🤖 Auto-replies sent: {auto_reply_count}")
    return posts



async def get_full_tweet_text(page, tweet_url):
    """Navigate to tweet page and extract full text."""
    full_text = None
    try:
        await page.goto(tweet_url, wait_until="domcontentloaded", timeout=10000)
        await random_delay(0.5, 1)
        article = await page.query_selector("article")
        if article:
            tweet_el = await article.query_selector("[data-testid=\"tweetText\"]")
            if tweet_el:
                full_text = await safe_text(tweet_el)
    except:
        pass
    return full_text

async def scrape_replies(page, tweet_url, max_replies=100):
    """Open a tweet and scrape replies, including nested/threaded replies."""
    replies = []
    try:
        await page.goto(tweet_url)
        await random_delay(2, 4)

        # Scroll to load replies (increased from 3 to 8)
        for _ in range(8):
            await human_scroll(page)
            await random_delay(1, 2)

        # Click "Show more replies" / "Show this thread" buttons up to 15 times
        expand_texts = [
            "Show more replies",
            "Show this thread",
            "Show additional replies",
        ]
        for _ in range(15):
            clicked = False
            for etxt in expand_texts:
                try:
                    btn = await page.query_selector(f'span:has-text("{etxt}")')
                    if not btn:
                        btn = await page.query_selector(f'div[role="button"]:has-text("{etxt}")')
                    if btn:
                        await btn.click()
                        await random_delay(1, 2)
                        clicked = True
                        break
                except:
                    continue
            if not clicked:
                break

        # Also try clicking generic "Show" / expand buttons
        for _ in range(10):
            try:
                expand_btns = await page.query_selector_all('div[role="button"]')
                clicked_any = False
                for btn in expand_btns:
                    txt = (await safe_text(btn)).lower()
                    if any(kw in txt for kw in ['show more', 'show replies', 'show this thread',
                                                  'show additional', 'more replies']):
                        await btn.click()
                        await random_delay(1, 2)
                        clicked_any = True
                        break
                if not clicked_any:
                    break
            except:
                break

        # Scroll again after expanding
        for _ in range(3):
            await human_scroll(page)
            await random_delay(0.5, 1)

        # Collect top-level replies
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

                reply_time = ""
                try:
                    time_el = await article.query_selector('time')
                    if time_el:
                        reply_time = await safe_attr(time_el, "datetime") or await safe_text(time_el) or ""
                except:
                    pass

                if text:
                    # Check for nested "Show replies" / "X replies" button within this reply
                    nested_replies = []
                    try:
                        # Look for nested reply indicators within the article's parent cell
                        parent_cell = article.evaluate_handle("el => el.closest('div[data-testid]') || el.parentElement")
                        if parent_cell:
                            nested_btns = await page.query_selector_all('div[role="button"]')
                            for nbtn in nested_btns:
                                ntxt = (await safe_text(nbtn)).lower()
                                # Match patterns like "3 replies", "Show replies", "1 reply"
                                if re.match(r'\d+\s+repl(?:y|ies)', ntxt) or 'show replies' in ntxt:
                                    try:
                                        await nbtn.click()
                                        await random_delay(1, 2)
                                        # Collect nested sub-replies that appear
                                        sub_articles = await page.query_selector_all('article')
                                        for sub in sub_articles:
                                            try:
                                                sub_user = await sub.query_selector('[data-testid="User-Name"]')
                                                sub_author = ""
                                                sub_handle = ""
                                                if sub_user:
                                                    sub_links = await sub_user.query_selector_all('a')
                                                    for sl in sub_links:
                                                        shref = await safe_attr(sl, "href")
                                                        stxt = await safe_text(sl)
                                                        if shref:
                                                            sub_handle = shref.rstrip('/').split('/')[-1]
                                                        if stxt and not is_x_noise(stxt):
                                                            sub_author = stxt
                                                            break
                                                sub_text_el = await sub.query_selector('[data-testid="tweetText"]')
                                                sub_text = await safe_text(sub_text_el) if sub_text_el else ""
                                                if sub_text:
                                                    nested_replies.append({
                                                        "author": sub_author,
                                                        "author_handle": sub_handle,
                                                        "text": sub_text,
                                                    })
                                            except:
                                                continue
                                        break  # Only click first matching nested button
                                    except:
                                        continue
                    except:
                        pass

                    reply_data = {
                        "author": author,
                        "author_handle": handle,
                        "text": text,
                        "timestamp": reply_time,
                    }
                    if nested_replies:
                        reply_data["nested_replies"] = nested_replies
                    replies.append(reply_data)
            except:
                continue
    except:
        pass
    return replies


# ── Vision Analysis ─────────────────────────────────────────────────

def download_x_file(url, timeout=15):
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            ct = resp.headers.get("Content-Type", "")
            ext = "jpg"
            if "png" in ct: ext = "png"
            elif "gif" in ct: ext = "gif"
            elif "webp" in ct: ext = "webp"
            elif "mp4" in ct: ext = "mp4"
            f = tempfile.NamedTemporaryFile(suffix=f".{ext}", delete=False)
            f.write(resp.read())
            f.close()
            return f.name
    except:
        return None


def analyze_single_x_image(url):
    """Download image and analyze using ZAI Vision API."""
    path = download_x_file(url)
    if not path:
        return f"[download failed]"
    try:
        result = zai_analyze_image(path)
        if result:
            return f"Vision: {result[:500]}"
        return "[image: no content detected]"
    except Exception as e:
        return f"[Vision error: {str(e)[:60]}]"
    finally:
        try: Path(path).unlink()
        except: pass


def analyze_image_urls(urls):
    """Analyze image URLs using ZAI Vision API."""
    results = []
    for url in urls[:3]:
        results.append(analyze_single_x_image(url))
    return [r for r in results if r]


# ── Engagement Detection ────────────────────────────────────────────

def detect_engagement_trigger(text):
    """Detect engagement triggers in tweet text (comment/reply/retweet prompts).
    Returns the keyword to reply with, or None."""
    if not text:
        return None
    patterns = [
        r"[Cc]omment\s+['\"]([^'\"]+)['\"]",            # Comment 'WORD'
        r"[Rr]eply\s+['\"]([^'\"]+)['\"]",               # Reply 'WORD'
        r"[Rr]etweet\s+and\s+[Rr]eply\s+['\"]?([^'\"\n,]+?)['\"]?(?:\s+for|\s*$)",  # retweet and reply X
        r"[Rr]eply\s+['\"]?([^'\"\n,]+?)['\"]?(?:\s+for|\s+to)",  # reply X for...
        r"[Qq]uote\s+[Tt]weet\s+['\"]([^'\"]+)['\"]",    # quote tweet 'WORD'
        r"[Rr]eply\s+with\s+['\"]([^'\"]+)['\"]",         # reply with 'WORD'
        r"[Cc]omment\s+['\"]?([^'\"\n,]+?)['\"]?(?:\s+for|\s+to|\s*$)",  # comment X for...
    ]
    for pattern in patterns:
        m = re.search(pattern, text)
        if m:
            keyword = m.group(1).strip()
            if keyword:
                return keyword
    return None


async def auto_engage_reply(page, tweet_url, keyword):
    """Auto-reply to a tweet with the given keyword.
    Returns True if successful, False otherwise."""
    try:
        # Navigate to the tweet
        await page.goto(tweet_url, wait_until="domcontentloaded", timeout=15000)
        await random_delay(2, 4)

        # Click the reply button (data-testid="reply")
        reply_btn = await page.query_selector('[data-testid="reply"]')
        if not reply_btn:
            # Fallback: click reply textarea
            reply_btn = await page.query_selector('[data-testid="tweetTextarea_0"]')
            if not reply_btn:
                reply_btn = await page.query_selector('[placeholder*="Reply"]')

        if not reply_btn:
            print("    ⚠️ Could not find reply button")
            return False

        await reply_btn.click()
        await random_delay(1, 2)

        # Find and focus the reply textarea
        reply_box = await page.query_selector('[data-testid="tweetTextarea_0"]')
        if not reply_box:
            reply_box = await page.query_selector('[contenteditable="true"][role="textbox"]')
        if not reply_box:
            print("    ⚠️ Could not find reply textbox")
            return False

        await reply_box.click()
        await random_delay(0.3, 0.6)

        # Type the keyword
        await page.keyboard.type(keyword, delay=random.randint(30, 80))
        await random_delay(0.5, 1)

        # Click the send/reply button
        send_btn = await page.query_selector('[data-testid="tweetButtonInline"]')
        if not send_btn:
            send_btn = await page.query_selector('[data-testid="tweetButton"]')
        if not send_btn:
            # Fallback: look for button with "Reply" or "Post" text
            send_btn = await page.query_selector('div[role="button"][data-testid="tweetButton"]')

        if not send_btn:
            print("    ⚠️ Could not find send button")
            return False

        await send_btn.click()
        await random_delay(2, 4)

        return True
    except Exception as e:
        print(f"    ⚠️ Auto-reply error: {str(e)[:80]}")
        return False


# ── Export ──────────────────────────────────────────────────────────

def export_data(posts, target_name, target_dir, skip_if_empty=True):
    if skip_if_empty and not posts:
        print("📁 No new posts — skipping export")
        return
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
    parser.add_argument("--known-ids-file", type=str, help="JSON file with known tweet IDs to skip")
    parser.add_argument("--export", choices=["json", "markdown", "both"], default="both")
    parser.add_argument("--auto-comment", action="store_true", help="Enable auto-reply on engagement triggers")
    parser.add_argument("--max-auto-comments", type=int, default=5, help="Max auto-replies per session (default: 5)")
    args = parser.parse_args()

    pw, browser, ctx, page = await create_browser(headless=args.headless)

    rate_limited = False
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

        # Load known IDs for incremental scraping
        known_ids = set()
        if args.known_ids_file:
            try:
                known_ids = set(json.loads(Path(args.known_ids_file).read_text()))
                print(f"📋 Loaded {len(known_ids)} known tweet IDs (incremental mode)")
            except Exception as e:
                print(f"⚠️ Could not load known IDs: {e}")

        posts = await scrape_target(page, args.url, limit=args.limit,
                                     do_replies=args.replies, do_images=args.images,
                                     known_ids=known_ids,
                                     auto_comment=args.auto_comment,
                                     max_auto_comments=args.max_auto_comments)

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

    except RateLimitError as e:
        print(f"\n🚨 Rate limited: {e} — skipping target, signaling cooldown extension")
        rate_limited = True
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
    if rate_limited:
        sys.exit(42)


def _silence_event_loop_closed():
    """Patch asyncio subprocess transports to suppress cosmetic
    'Event loop is closed' errors on Python 3.12+ GC during shutdown.
    The error fires in BaseSubprocessTransport.__del__ AFTER all work
    is complete — it's harmless noise that corrupts our exit code."""
    try:
        from asyncio.base_subprocess import BaseSubprocessTransport
        orig_del = BaseSubprocessTransport.__del__

        def _patched_del(self):
            try:
                orig_del(self)
            except RuntimeError:
                pass

        BaseSubprocessTransport.__del__ = _patched_del
    except Exception:
        pass


if __name__ == "__main__":
    _silence_event_loop_closed()
    asyncio.run(main())
