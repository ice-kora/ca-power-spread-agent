# -*- coding: utf-8 -*-
"""
tools/screenshot_prototype.py —— V0.4 设计冲刺：design_prototypes 静态稿截图

对 design_prototypes/*.html 逐一用 Selenium（1440×900）截图到 design_shots/。

用法（仓库根目录）：
    python tools/screenshot_prototype.py            # 截 design_prototypes/*.html
    python tools/screenshot_prototype.py --file 02  # 只截名字含 02 的
"""

from __future__ import annotations

import argparse
import glob
import os
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
os.chdir(REPO_ROOT)

OUT_DIR = REPO_ROOT / "design_shots"
DRIVER_CACHE = Path(os.environ.get("TEMP", ".")) / "cdriver_151" / "chromedriver-win64" / "chromedriver.exe"


def _driver_path() -> str:
    if DRIVER_CACHE.exists():
        return str(DRIVER_CACHE)
    import io
    import urllib.request
    import zipfile
    cache = Path(os.environ.get("TEMP", ".")) / "cdriver_151"
    url = "https://storage.googleapis.com/chrome-for-testing-public/151.0.7922.77/win64/chromedriver-win64.zip"
    print("[driver] 下载 chromedriver 151 …")
    data = urllib.request.urlopen(url, timeout=120).read()
    zipfile.ZipFile(io.BytesIO(data)).extractall(cache)
    return str(cache / "chromedriver-win64" / "chromedriver.exe")


def main() -> int:
    ap = argparse.ArgumentParser(description="design_prototypes 静态稿截图")
    ap.add_argument("--file", default="", help="只截文件名包含该串的")
    ap.add_argument("--width", type=int, default=1440)
    ap.add_argument("--height", type=int, default=900)
    args = ap.parse_args()

    from selenium import webdriver
    from selenium.webdriver.chrome.service import Service

    htmls = sorted(glob.glob(str(REPO_ROOT / "design_prototypes" / "*.html")))
    if args.file:
        htmls = [h for h in htmls if args.file in os.path.basename(h)]
    if not htmls:
        print("design_prototypes/ 下无 .html 文件")
        return 1

    opts = webdriver.ChromeOptions()
    opts.add_argument("--headless")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument(f"--window-size={args.width},{args.height}")
    driver = webdriver.Chrome(service=Service(executable_path=_driver_path()), options=opts)
    driver.set_window_size(args.width, args.height)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    try:
        for h in htmls:
            name = Path(h).stem
            url = Path(h).resolve().as_uri()
            driver.get(url)
            time.sleep(1.2)
            out = OUT_DIR / f"{name}_{args.width}x{args.height}.png"
            driver.save_screenshot(str(out))
            print(f"[shot] {out}")
        print("DONE")
        return 0
    finally:
        driver.quit()


if __name__ == "__main__":
    sys.exit(main())
