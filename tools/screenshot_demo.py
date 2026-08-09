# -*- coding: utf-8 -*-
"""
tools/screenshot_demo.py —— V0.3.2 浏览器验收截图（Selenium）

需求 35/36：在真实浏览器（1920×1080）验证并输出 8 张演示截图：
  S1 初始页面
  S2 SELL Recommendation Hero（案例 A）
  S3 为什么这样建议
  S4 Agent streaming 中
  S5 Agent 回答完成 + 工作轨迹
  S6 Case E Time Gate（未来信息被拒绝）
  S7 Lock 后
  S8 Reveal + Loss + Post-trade Review

用法（仓库根目录）：
    python tools/screenshot_demo.py            # 自动下载匹配 chromedriver + 启动 demo server
    python tools/screenshot_demo.py --driver <chromedriver.exe>   # 指定 driver
输出：demo_shots/（S1..S8）
"""

from __future__ import annotations

import argparse
import io
import os
import sys
import tempfile
import threading
import time
import urllib.request
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
os.chdir(REPO_ROOT)

# 用 DEMO 模式（真实历史快照；Case E 显示被拒证据；不联网）
os.environ["MVP_DATA_MODE"] = "demo"
OUT_DIR = REPO_ROOT / "demo_shots"
PORT = 5123


def _download_driver() -> str:
    """下载匹配 Chrome 版本的 chromedriver（Selenium Manager 不覆盖 PATH 旧版时兜底）。"""
    cache = Path(tempfile.gettempdir()) / "cdriver_151"
    drv = cache / "chromedriver-win64" / "chromedriver.exe"
    if drv.exists():
        return str(drv)
    url = "https://storage.googleapis.com/chrome-for-testing-public/151.0.7922.77/win64/chromedriver-win64.zip"
    print("[driver] 下载 chromedriver 151 …")
    data = urllib.request.urlopen(url, timeout=120).read()
    z = zipfile.ZipFile(io.BytesIO(data))
    z.extractall(cache)
    print("[driver] ->", drv)
    return str(drv)


def _port_free(port: int) -> bool:
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def _start_server() -> None:
    import mvp_web  # noqa: PLC0415  # 在 MVP_DATA_MODE=demo 之后 import
    mvp_web.app.run(host="127.0.0.1", port=PORT, debug=False, use_reloader=False)


def _by_id(driver, eid):
    from selenium.webdriver.common.by import By
    return driver.find_element(By.ID, eid)


def _select(driver, eid, value):
    from selenium.webdriver.support.ui import Select
    Select(_by_id(driver, eid)).select_by_value(value)


def _shot(driver, name):
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / name
    driver.save_screenshot(str(path))
    print(f"[shot] {path}")
    return path


def _wait_hero(driver, timeout=30):
    """轮询等待主决策 Hero 渲染（demo 首次装载可能较慢）。"""
    from selenium.webdriver.common.by import By
    for _ in range(timeout):
        if driver.find_elements(By.CSS_SELECTOR, ".hero"):
            return True
        time.sleep(1)
    return False


def _reload_and_run(driver, case, wait_hero=True):
    """重载页面（重置状态）→ 选案例 → 生成交易建议。"""
    driver.get(f"http://127.0.0.1:{PORT}/")
    time.sleep(2)
    _select(driver, "inp-case", case)
    _by_id(driver, "btn-run").click()
    if wait_hero:
        return _wait_hero(driver)
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description="V0.3.2 浏览器验收截图")
    ap.add_argument("--driver", default="", help="chromedriver 可执行文件路径（缺省自动下载）")
    args = ap.parse_args()

    from selenium import webdriver
    from selenium.webdriver.chrome.service import Service
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC

    drv = args.driver or _download_driver()
    if not _port_free(PORT):
        print(f"[warn] 端口 {PORT} 被占用，请检查是否有残留进程；改用其他端口。")
        return 1
    threading.Thread(target=_start_server, daemon=True).start()
    time.sleep(3)  # 等 server 起来

    opts = webdriver.ChromeOptions()
    opts.add_argument("--headless")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--window-size=1920,1080")
    opts.add_argument("--force-device-scale-factor=1")
    driver = webdriver.Chrome(service=Service(executable_path=drv), options=opts)
    driver.set_window_size(1920, 1080)
    wait = WebDriverWait(driver, 15)

    try:
        driver.get(f"http://127.0.0.1:{PORT}/")
        wait.until(EC.presence_of_element_located((By.ID, "btn-run")))
        time.sleep(1)
        _shot(driver, "S1_initial.png")

        # ---- S2/S3：案例 A（SELL Hero + 为什么） ----
        driver.execute_script("""
          window.__errs=[];
          window.onerror=function(m,s,l,c,e){window.__errs.push('err:'+m+' @'+l+':'+c);};
          window.onunhandledrejection=function(e){window.__errs.push('rej:'+String(e.reason));};
        """)
        _select(driver, "inp-case", "B")
        _by_id(driver, "btn-run").click()
        if not _wait_hero(driver):
            print("[warn] .hero 未渲染", driver.execute_script("return window.__errs"))
            raise RuntimeError(".hero 未渲染")
        driver.execute_script("window.scrollTo(0, 0)")
        _shot(driver, "S2_sell_hero.png")
        driver.execute_script("document.querySelector('.why-grid')?.scrollIntoView({block:'center'})")
        time.sleep(0.6)
        _shot(driver, "S3_why.png")

        # ---- S4/S5：Agent streaming ----
        driver.execute_script("document.querySelector('.agent-quick button')?.click(); true")
        time.sleep(0.6)
        driver.execute_script("document.querySelector('.agent-panel')?.scrollIntoView({block:'center'})")
        _shot(driver, "S4_agent_streaming.png")
        time.sleep(3)
        driver.execute_script("document.querySelector('.agent-chat')?.scrollIntoView({block:'center'})")
        time.sleep(0.5)
        _shot(driver, "S5_agent_done.png")

        # ---- S6：Case E Time Gate ----
        _reload_and_run(driver, "E")
        driver.execute_script(
            "var items=document.querySelectorAll('.card'); var t=[...items].find(c=>c.textContent.includes('外部信息')); "
            "t&&t.scrollIntoView({block:'center'}); true")
        time.sleep(0.6)
        _shot(driver, "S6_caseE_timegate.png")

        # ---- S7/S8：Lock + Reveal + Review（用案例 D 模型判断错误） ----
        _reload_and_run(driver, "D")
        driver.execute_script("document.querySelector('#btn-lock')?.click(); true")
        for _ in range(20):
            if "已锁定" in _by_id(driver, "status-badge").text:
                break
            time.sleep(1)
        time.sleep(0.5)
        driver.execute_script("document.querySelector('.lock-bar')?.scrollIntoView({block:'center'}); true")
        time.sleep(0.3)
        _shot(driver, "S7_locked.png")
        driver.execute_script("document.querySelector('#btn-reveal')?.click(); true")
        for _ in range(20):
            if driver.find_elements(By.CSS_SELECTOR, ".result-box"):
                break
            time.sleep(1)
        time.sleep(0.5)
        driver.execute_script("document.querySelector('.result-box')?.scrollIntoView({block:'center'}); true")
        time.sleep(0.3)
        _shot(driver, "S8_reveal_review.png")

        # 1440×900 首页验收
        driver.set_window_size(1440, 900)
        driver.get(f"http://127.0.0.1:{PORT}/")
        time.sleep(1)
        _shot(driver, "V1_1440x900_initial.png")
        print("DONE —— 全部截图已输出到 demo_shots/")
        return 0
    finally:
        driver.quit()


if __name__ == "__main__":
    sys.exit(main())
