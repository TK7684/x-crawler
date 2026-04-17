#!/usr/bin/env python3
"""
Vision analyzer — downloads images from URLs and extracts content
using ZAI vision API (default) with local OCR fallback.

Called by scraper.py with a JSON file of image URLs.
Output: JSON array of descriptions, one per image.
"""

import base64
import json
import os
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

# ---------------------------------------------------------------------------
# Config — mirrors nlp_config.py pattern
# ---------------------------------------------------------------------------

ZAI_API_BASE = os.getenv("ZAI_API_BASE", "https://open.bigmodel.cn/api/paas/v4")
ZAI_VISION_MODEL = os.getenv("ZAI_VISION_MODEL", "glm-4v-flash")
_ZAI_KEY_CACHE = None


def _get_zai_key() -> str:
    global _ZAI_KEY_CACHE
    if _ZAI_KEY_CACHE:
        return _ZAI_KEY_CACHE

    key = os.getenv("ZAI_API_KEY")
    if key:
        _ZAI_KEY_CACHE = key
        return key

    # Try .env in same directory
    env_file = Path(__file__).parent / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.startswith("ZAI_API_KEY="):
                _ZAI_KEY_CACHE = line.split("=", 1)[1].strip().strip('"')
                return _ZAI_KEY_CACHE

    # GCP Secret Manager fallback
    try:
        result = subprocess.run(
            ["bash", "-c", "gcloud secrets versions access latest --secret=zai-api-key --project=luna-workspace"],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0 and result.stdout.strip():
            _ZAI_KEY_CACHE = result.stdout.strip()
            return _ZAI_KEY_CACHE
    except Exception:
        pass

    return ""


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------

def download_image(url, timeout=15):
    """Download image to temp file, return path."""
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read()
        ct = resp.headers.get("Content-Type", "image/jpeg")
        ext = "jpg"
        if "png" in ct:
            ext = "png"
        elif "gif" in ct:
            ext = "gif"
        elif "webp" in ct:
            ext = "webp"

        tmp = tempfile.NamedTemporaryFile(suffix=f".{ext}", delete=False)
        tmp.write(data)
        tmp.close()
        return tmp.name


# ---------------------------------------------------------------------------
# ZAI Vision API (default)
# ---------------------------------------------------------------------------

def analyze_with_zai(image_path: str) -> str:
    """Analyze image using ZAI vision model (glm-4v-flash)."""
    api_key = _get_zai_key()
    if not api_key:
        return ""

    with open(image_path, "rb") as f:
        image_b64 = base64.b64encode(f.read()).decode("ascii")

    ext = Path(image_path).suffix.lstrip(".")
    mime = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
            "gif": "image/gif", "webp": "image/webp"}.get(ext, "image/jpeg")

    payload = json.dumps({
        "model": ZAI_VISION_MODEL,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{image_b64}"}},
                {"type": "text", "text": (
                    "Describe this image concisely. Extract any visible text (OCR). "
                    "If it contains a chart or data, summarize the key numbers. "
                    "If it contains a screenshot, describe the UI and content. "
                    "Reply in the same language as any text in the image. Max 200 words."
                )}
            ]
        }],
        "temperature": 0.3,
        "max_tokens": 300,
    }).encode("utf-8")

    req = urllib.request.Request(
        f"{ZAI_API_BASE}/chat/completions",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            return body["choices"][0]["message"]["content"].strip()
    except Exception as e:
        return f"[ZAI vision error: {e}]"


# ---------------------------------------------------------------------------
# Local fallback (Tesseract OCR + PIL)
# ---------------------------------------------------------------------------

def analyze_with_local(image_path: str) -> str:
    """Fallback: Tesseract OCR + basic PIL info."""
    results = []

    # OCR
    try:
        result = subprocess.run(
            ["tesseract", image_path, "stdout", "-l", "tha+eng"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            lines = [l.strip() for l in result.stdout.split("\n") if l.strip() and len(l.strip()) > 1]
            if lines:
                results.append(f"[OCR Text]\n{chr(10).join(lines)}")
    except Exception:
        pass

    # Image info
    try:
        from PIL import Image
        img = Image.open(image_path)
        w, h = img.size
        info = f"{w}x{h} {img.mode}"
        if img.format:
            info = f"{img.format} {info}"
        results.append(f"[Image Info] {info}")
    except Exception:
        pass

    return "\n".join(results) if results else "[No content extracted]"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def analyze_image(image_path: str) -> str:
    """Analyze image: ZAI vision first, local fallback."""
    result = analyze_with_zai(image_path)
    if result and not result.startswith("[ZAI vision error"):
        return result
    # Fallback to local
    local = analyze_with_local(image_path)
    if result.startswith("[ZAI vision error"):
        return f"{result}\n{local}"
    return local


def main():
    if len(sys.argv) < 2:
        print("Usage: vision_analyze.py <urls.json>")
        sys.exit(1)

    urls_file = Path(sys.argv[1])
    if not urls_file.exists():
        print("[]")
        return

    urls = json.loads(urls_file.read_text(encoding="utf-8"))
    analyses = []

    for url in urls[:5]:  # max 5 images per post
        try:
            path = download_image(url)
            desc = analyze_image(path)
            analyses.append(desc)
            Path(path).unlink(missing_ok=True)
        except Exception as e:
            analyses.append(f"[Download failed: {e}]")

    print(json.dumps(analyses, ensure_ascii=False))


if __name__ == "__main__":
    main()
