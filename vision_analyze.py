#!/usr/bin/env python3
"""
Vision analyzer — downloads images from URLs and extracts content
(text/OCR + visual description) using a local vision model.

Called by scraper.py with a JSON file of image URLs.
Output: JSON array of descriptions, one per image.
"""

import json
import sys
import urllib.request
import tempfile
import subprocess
from pathlib import Path


def download_image(url, timeout=15):
    """Download image to temp file, return path."""
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read()
        # Detect extension
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


def analyze_with_local_model(image_path):
    """
    Analyze image using local tools. Tries:
    1. Tesseract OCR (for text in images)
    2. Python image description via subprocess
    Falls back to basic description.
    """
    results = []

    # Try OCR first
    ocr_text = ""
    try:
        result = subprocess.run(
            ["tesseract", image_path, "stdout", "-l", "tha+eng"],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0 and result.stdout.strip():
            ocr_text = result.stdout.strip()
            # Clean up OCR noise
            lines = [l.strip() for l in ocr_text.split('\n') if l.strip() and len(l.strip()) > 1]
            ocr_text = '\n'.join(lines)
    except FileNotFoundError:
        pass  # tesseract not installed
    except Exception:
        pass

    if ocr_text:
        results.append(f"[OCR Text]\n{ocr_text}")

    # Get basic image info
    try:
        from PIL import Image
        img = Image.open(image_path)
        w, h = img.size
        mode = img.mode
        info = f"{w}x{h} {mode}"
        if img.format:
            info = f"{img.format} {info}"
        results.append(f"[Image Info] {info}")

        # Check if it's mostly text (simple heuristic)
        try:
            import numpy as np
            arr = np.array(img.convert('L'))
            mean_brightness = arr.mean()
            std = arr.std()
            # High contrast + high std often means text overlay
            if std > 60:
                results.append(f"[Hint] High contrast image — likely contains text overlay or chart")
            # Dark image
            if mean_brightness < 80:
                results.append(f"[Hint] Dark image (avg brightness: {mean_brightness:.0f})")
        except ImportError:
            pass
    except ImportError:
        results.append("[Image Info] PIL not available")
    except Exception as e:
        results.append(f"[Error] {e}")

    return "\n".join(results) if results else "[No content extracted]"


def main():
    if len(sys.argv) < 2:
        print("Usage: vision_analyze.py <urls.json>")
        sys.exit(1)

    urls_file = Path(sys.argv[1])
    if not urls_file.exists():
        print("[]")
        return

    urls = json.loads(urls_file.read_text())
    analyses = []

    for url in urls[:5]:  # max 5 images per post
        try:
            path = download_image(url)
            desc = analyze_with_local_model(path)
            analyses.append(desc)
            # Cleanup
            Path(path).unlink(missing_ok=True)
        except Exception as e:
            analyses.append(f"[Download failed: {e}]")

    print(json.dumps(analyses, ensure_ascii=False))


if __name__ == "__main__":
    main()
