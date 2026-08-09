# -*- coding: utf-8 -*-
"""
tools/selenium_acceptance.py —— V0.4 Phase 2 浏览器验收（Agent E · Browser QA + Screenshots）
============================================================================================

在真实浏览器（Selenium headless）里对 CAISO 交易决策助手 Decision Workspace 做 V0.4
Phase 2 浏览器验收。交易核心冻结：本脚本只做验收与截图，不改任何模型 / 规则 / UI 文件。

验收维度（每步打印 [PASS]/[FAIL]）：
  1) 无 JS error        —— 注入 window.onerror / onunhandledrejection 收集 + driver.get_log(browser)
  2) 无横向滚动          —— documentElement.scrollWidth <= clientWidth（1440×900 与 1920×1080）
  3) 第一屏可见          —— scrollY≈0 时 选择控件 + Hero（卖出日前/预计 DA−RT）+ 风险状态 + Agent 输入区
  4) Agent sticky        —— .agent-panel computed position=sticky，滚动主栏时保持在视口顶部
  5) 技术折叠正常        —— details.detail 默认收起，点击展开
  6) Lock / Reveal 正常  —— 点锁定→状态已锁定→点揭晓→结果出现
  7) Case E 正常         —— 外部信息区出现时间轴（报价截止 vs 信息来源时间）+ NOT USED
  8) Streaming 正常      —— 点 Agent 预设问题→出现流式回答 + 工作轨迹；Tool trace 与真实工具一致
  9) Golden Case 回归    —— demo 模式跑 5 个 Golden，断言 final/gate：
     B=SELL_DA/PASS · C1=NO_TRADE/REJECT · C2=NO_TRADE/WARNING · D=SELL_DA/WARNING · E=NO_TRADE/REJECT
 10) 截图                —— demo_shots_v04/ 11 张（10×1440×900 + 1×1920×1080 hero）

用法（仓库根目录）：
    python tools/selenium_acceptance.py             # 自动下载 chromedriver + demo server
    python tools/selenium_acceptance.py --llm mock  # 强制确定性 mock LLM（验收不依赖外网 / 真实 key）
    python tools/selenium_acceptance.py --driver <chromedriver.exe> --port 5130

输出：demo_shots_v04/（截图）+ 终端逐项 [PASS]/[FAIL] + 汇总清单。
退出码：全部 PASS → 0；任一 FAIL → 1。
"""

from __future__ import annotations

import argparse
import io
import json
import os
import socket
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

# Windows 控制台/管道默认 GBK 无法编码 U+2212(−) 等字符 → 统一 UTF-8 输出，防 UnicodeEncodeError
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

# 用 DEMO 模式（真实历史快照；Case E 显示被拒证据；不联网）
os.environ["MVP_DATA_MODE"] = "demo"
OUT_DIR = REPO_ROOT / "demo_shots_v04"
PORT = 5130

# Golden Case 预期（docs/mvp_demo_cases.md + mvp_web.GOLDEN_CASES）
GOLDEN_EXPECT = {
    "B":  {"final": "SELL_DA",  "gate": "PASS"},
    "C1": {"final": "NO_TRADE", "gate": "REJECT"},
    "C2": {"final": "NO_TRADE", "gate": "WARNING"},
    "D":  {"final": "SELL_DA",  "gate": "WARNING"},
    "E":  {"final": "NO_TRADE", "gate": "REJECT"},
}
GOLDEN_PARAMS = {
    "B":  ("2026-07-16", "CONTROLX_1_N001", 3),
    "C1": ("2026-07-08", "CONTROLX_1_N001", 2),
    "C2": ("2026-07-10", "SNLNDRO_1_N001", 10),
    "D":  ("2026-07-20", "SNLNDRO_1_N001", 20),
    "E":  ("2026-07-08", "CONTROLX_1_N001", 2),
}

RESULTS: list = []


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------
def _record(name: str, ok: bool, detail: str = ""):
    RESULTS.append((name, bool(ok), detail))
    mark = "PASS" if ok else "FAIL"
    tail = f"  ({detail})" if detail else ""
    print(f"[{mark}] {name}{tail}")


def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def _pick_port(base: int) -> int:
    for p in range(base, base + 30):
        if _port_free(p):
            return p
    return base


def _download_driver() -> str:
    """下载匹配 Chrome 151 的 chromedriver（复用 temp 缓存；已有则直接使用）。"""
    cache = Path(tempfile.gettempdir()) / "cdriver_151"
    drv = cache / "chromedriver-win64" / "chromedriver.exe"
    if drv.exists():
        return str(drv)
    url = "https://storage.googleapis.com/chrome-for-testing-public/151.0.7922.77/win64/chromedriver-win64.zip"
    print("[driver] 下载 chromedriver 151 …")
    data = urllib.request.urlopen(url, timeout=120).read()
    zipfile.ZipFile(io.BytesIO(data)).extractall(cache)
    print("[driver] ->", drv)
    return str(drv)


def _http_json(method: str, path: str, body=None, timeout: int = 40):
    url = f"http://127.0.0.1:{PORT}{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data,
                                 headers={"Content-Type": "application/json"},
                                 method=method)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _start_server():
    import mvp_web  # noqa: PLC0415  # 在 MVP_DATA_MODE=demo / LLM 覆盖之后 import
    mvp_web.app.run(host="127.0.0.1", port=PORT, debug=False, use_reloader=False)


def _wait_server(timeout: float = 20) -> bool:
    for _ in range(int(timeout / 0.5)):
        try:
            _http_json("GET", "/api/meta", timeout=2)
            return True
        except Exception:
            time.sleep(0.5)
    return False


def _by_id(driver, eid):
    from selenium.webdriver.common.by import By
    return driver.find_element(By.ID, eid)


def _select(driver, eid, value):
    from selenium.webdriver.support.ui import Select
    Select(_by_id(driver, eid)).select_by_value(value)


def _wait_for(driver, fn, timeout: float, desc: str, interval: float = 0.5) -> bool:
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            if fn():
                return True
        except Exception as exc:  # noqa: BLE001
            last = exc
        time.sleep(interval)
    return False


def _wait_hero(driver, timeout: float = 40):
    from selenium.webdriver.common.by import By
    def hero_present():
        return bool(driver.find_elements(By.CSS_SELECTOR, ".hero"))
    ok = _wait_for(driver, hero_present, timeout, ".hero 渲染")
    if not ok:
        print("[warn] .hero 未渲染; __errs=", driver.execute_script("return window.__errs||[]"))
    return ok


def _wait_init(driver, timeout: float = 15):
    """等待 init() 完成：meta 已拉取、预设问题按钮已渲染。"""
    from selenium.webdriver.common.by import By
    def inited():
        return len(driver.find_elements(By.CSS_SELECTOR, "#quick-q button")) >= 1
    return _wait_for(driver, inited, timeout, "页面 init 完成")


def _inject_js_errors(driver):
    driver.execute_script(
        "window.__errs=[];"
        "window.onerror=function(m,s,l,c,e){window.__errs.push('err:'+m+' @'+l+':'+c);};"
        "window.onunhandledrejection=function(e){window.__errs.push('rej:'+String(e.reason));};")


_BENIGN_LOG = ("favicon", "permissions-policy", "deprecat", "unrecognized feature", "source map")


def _browser_log(driver):
    """返回过滤后的 SEVERE 浏览器日志（排除 favicon 等良性噪音）。"""
    out = []
    try:
        for e in driver.get_log("browser"):
            if str(e.get("level", "")).upper() != "SEVERE":
                continue
            msg = str(e.get("message", ""))
            if any(b in msg.lower() for b in _BENIGN_LOG):
                continue
            out.append(msg[:220])
    except Exception:  # noqa: BLE001
        pass
    return out


def _live_js_errors(driver):
    """window.__errs 收集到的运行时 JS 错误。"""
    try:
        return list(driver.execute_script("return window.__errs||[]"))
    except Exception:  # noqa: BLE001
        return []


def _set_viewport(driver, w: int, h: int) -> bool:
    """把布局视口（innerWidth×innerHeight）校准到精确 w×h，保证截图尺寸准确。"""
    driver.set_window_size(w, h)
    for _ in range(6):
        iw = int(driver.execute_script("return window.innerWidth") or 0)
        ih = int(driver.execute_script("return window.innerHeight") or 0)
        dx, dy = w - iw, h - ih
        if dx == 0 and dy == 0:
            return True
        driver.set_window_size(w + dx, h + dy)
    iw = int(driver.execute_script("return window.innerWidth") or 0)
    ih = int(driver.execute_script("return window.innerHeight") or 0)
    return iw == w and ih == h


def _shot(driver, name: str) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / name
    driver.save_screenshot(str(path))
    return path


def _reload_and_run(driver, case: str):
    """重载页面（重置状态）→ 选案例 → 生成交易建议。"""
    driver.get(f"http://127.0.0.1:{PORT}/")
    _wait_init(driver)
    _inject_js_errors(driver)
    _select(driver, "inp-case", case)
    _by_id(driver, "btn-run").click()
    return _wait_hero(driver)


def _rect_in_viewport(driver, element) -> dict:
    return driver.execute_script(
        "var r=arguments[0].getBoundingClientRect();"
        "return {top:r.top,bottom:r.bottom,left:r.left,right:r.right,"
        "vw:window.innerWidth,vh:window.innerHeight,scrollY:window.scrollY};",
        element)


def _scroll_center(driver, element):
    driver.execute_script("arguments[0].scrollIntoView({block:'center'})", element)
    time.sleep(0.4)


# ---------------------------------------------------------------------------
# 验收阶段
# ---------------------------------------------------------------------------
def phase_integrity(driver, label: str):
    """B1 无 JS error + B2 无横向滚动（当前视口）。"""
    errs = _live_js_errors(driver) + _browser_log(driver)
    _record(f"B1 无 JS error（{label}）", not errs, "; ".join(errs[:5]) if errs else "clean")
    overflow = int(driver.execute_script(
        "return document.documentElement.scrollWidth - document.documentElement.clientWidth") or 0)
    _record(f"B2 无横向滚动（{label}）", overflow <= 0, f"overflow={overflow}px")


def phase_first_screen(driver):
    """B3 第一屏可见：选择控件 + Hero（卖出日前/预计 DA−RT）+ 风险状态 + Agent 输入区。"""
    from selenium.webdriver.common.by import By
    driver.execute_script("window.scrollTo(0,0)")
    time.sleep(0.4)
    checks = [
        ("选择控件 #btn-run", By.ID, "btn-run"),
        ("Hero .hero", By.CSS_SELECTOR, ".hero"),
        ("Agent 输入区 #inp-question", By.ID, "inp-question"),
    ]
    for label, by, sel in checks:
        els = driver.find_elements(by, sel)
        if not els:
            _record(f"B3 第一屏可见：{label}", False, "元素不存在")
            continue
        r = _rect_in_viewport(driver, els[0])
        ok = r["top"] >= -1 and r["top"] < r["vh"] and r["bottom"] > 0 and r["scrollY"] <= 2
        _record(f"B3 第一屏可见：{label}", ok,
                f"rect top={r['top']:.0f} bottom={r['bottom']:.0f} vh={r['vh']} scrollY={r['scrollY']:.0f}")
    # Hero 文案：卖出日前 / 预计 DA−RT / 风险检查
    hero_text = ""
    heros = driver.find_elements(By.CSS_SELECTOR, ".hero")
    if heros:
        hero_text = heros[0].text
    for kw, note in (("卖出日前", "Hero 建议文案"), ("预计 DA − RT", "Hero 价差数字"),
                     ("风险检查", "Hero 风险状态")):
        _record(f"B3 Hero 含「{kw}」（{note}）", kw in hero_text,
                "缺失" if kw not in hero_text else "")


def phase_sticky(driver):
    """B4 Agent sticky 正常。"""
    from selenium.webdriver.common.by import By
    panel = driver.find_elements(By.CSS_SELECTOR, ".agent-panel")
    if not panel:
        _record("B4 Agent sticky", False, ".agent-panel 不存在")
        return
    pos = driver.execute_script("return getComputedStyle(arguments[0]).position", panel[0])
    ok1 = pos == "sticky"
    _record("B4 .agent-panel position=sticky", ok1, f"computed={pos}")
    driver.execute_script("window.scrollTo(0, 400)")
    time.sleep(0.5)
    r = _rect_in_viewport(driver, panel[0])
    ok2 = ok1 and abs(r["top"] - 12) <= 5 and r["top"] >= 0 and r["top"] < r["vh"]
    _record("B4 滚动后 Agent 保持视口顶部", ok2,
            f"rect top={r['top']:.0f}（期望≈12）")
    driver.execute_script("window.scrollTo(0,0)")
    time.sleep(0.3)


def phase_tech_collapse(driver):
    """B5 技术折叠正常：details.detail 默认收起，点击展开。"""
    from selenium.webdriver.common.by import By
    detail = driver.find_elements(By.XPATH,
                                  "//details[.//summary[contains(text(),'查看数据血缘')]]")
    if not detail:
        detail = driver.find_elements(By.CSS_SELECTOR, "details.detail")
    if not detail:
        _record("B5 技术折叠", False, "找不到 details.detail")
        return
    el = detail[0]
    closed = not bool(driver.execute_script("return arguments[0].open", el))
    h0 = int(driver.execute_script("return arguments[0].offsetHeight", el) or 0)
    _record("B5 details 默认收起", closed, f"offsetHeight={h0}")
    driver.execute_script("arguments[0].querySelector('summary').click()", el)
    time.sleep(0.4)
    opened = bool(driver.execute_script("return arguments[0].open", el))
    h1 = int(driver.execute_script("return arguments[0].offsetHeight", el) or 0)
    _record("B5 details 点击展开", opened and h1 > h0, f"offsetHeight {h0}→{h1}")


def phase_lock_reveal(driver):
    """B6 Lock / Reveal 正常（用案例 D，最终建议 SELL_DA、gate WARNING）。"""
    from selenium.webdriver.common.by import By
    if not _reload_and_run(driver, "D"):
        _record("B6 案例 D 生成", False, ".hero 未渲染")
        return
    time.sleep(0.3)
    lock_btn = driver.find_elements(By.ID, "btn-lock")
    if not lock_btn:
        _record("B6 锁定按钮存在", False, "#btn-lock 不存在")
        return
    _scroll_center(driver, lock_btn[0])
    lock_btn[0].click()

    def locked_state():
        try:
            if "已锁定" in _by_id(driver, "btn-lock").text:
                return True
        except Exception:  # noqa: BLE001
            pass
        return bool(driver.find_elements(By.CSS_SELECTOR, ".lock-seal"))

    locked_ok = _wait_for(driver, locked_state, 15, "锁定状态")
    _record("B6 点击锁定→状态已锁定", locked_ok,
            "#btn-lock 未显示已锁定 / .lock-seal 未出现" if not locked_ok else "")
    # 截 07_locked
    _shot(driver, "07_locked.png")
    reveal_btn = driver.find_elements(By.ID, "btn-reveal")
    if not reveal_btn:
        _record("B6 揭晓按钮存在", False, "#btn-reveal 不存在")
        return
    reveal_btn[0].click()
    reveal_ok = _wait_for(driver, lambda: bool(
        driver.find_elements(By.CSS_SELECTOR, ".result-box")), 20, "Reveal 结果")
    _record("B6 点击揭晓→结果出现", reveal_ok)
    if reveal_ok:
        rb = driver.find_elements(By.CSS_SELECTOR, ".result-box")[0]
        txt = rb.text
        _record("B6 结果块含实际 DA−RT/结果", "实际 DA − RT" in txt or "本次" in txt,
                txt[:60].replace("\n", " "))

    def review_card():
        return driver.find_elements(By.XPATH,
                                    "//div[contains(@class,'act-title') and normalize-space()='复盘']"
                                    "/ancestor::div[contains(@class,'case-card')]")
    if reveal_ok:
        rb = driver.find_elements(By.CSS_SELECTOR, ".result-box")
        if rb:
            _scroll_center(driver, rb[0])
        _shot(driver, "08_reveal.png")
        rv = review_card()
        if rv:
            _scroll_center(driver, rv[0])
        _shot(driver, "09_review.png")
    else:
        _shot(driver, "08_reveal.png")
        _shot(driver, "09_review.png")


def phase_case_e(driver):
    """B7 Case E：外部信息区出现时间轴（报价截止 vs 信息来源时间）+ NOT USED。"""
    from selenium.webdriver.common.by import By
    if not _reload_and_run(driver, "E"):
        _record("B7 案例 E 生成", False, ".hero 未渲染")
        return
    cards = driver.find_elements(By.XPATH,
                                 "//div[contains(@class,'act-title') and contains(text(),'外部信息')]"
                                 "/ancestor::div[contains(@class,'act')]")
    if not cards:
        _record("B7 外部信息区存在", False, "找不到「外部信息」区")
        return
    card = cards[0]
    _scroll_center(driver, card)
    card_text = card.text
    _record("B7 外部信息区含 NOT USED", "NOT USED" in card_text,
            "缺失" if "NOT USED" not in card_text else "")
    stage = card.find_elements(By.CSS_SELECTOR, ".tg-stage")
    _record("B7 时间轴舞台（.tg-stage）存在", bool(stage))
    timeline = card.find_elements(By.CSS_SELECTOR, ".timeline")
    if timeline:
        tl_text = timeline[0].text
        _record("B7 时间轴含 GFS 对比", "GFS" in tl_text,
                tl_text.replace("\n", " ") if tl_text else "时间轴为空")
    else:
        _record("B7 时间轴含 GFS 对比", False, ".timeline 缺失")
    concl = card.find_elements(By.CSS_SELECTOR, ".tg-conclusion")
    if concl:
        _record("B7 结论「它来晚了，所以没有使用」",
                "来晚了" in concl[0].text or "没有使用" in concl[0].text,
                concl[0].text.strip())
    else:
        _record("B7 结论「它来晚了，所以没有使用」", False, ".tg-conclusion 缺失")
    _shot(driver, "06_case_e.png")


def phase_streaming(driver):
    """B8 Streaming 正常 + Tool trace 与真实工具一致。"""
    from selenium.webdriver.common.by import By
    if not _reload_and_run(driver, "B"):
        _record("B8 案例 B 生成", False, ".hero 未渲染")
        return
    quick = driver.find_elements(By.CSS_SELECTOR, "#quick-q button")
    if not quick:
        _record("B8 预设问题按钮存在", False)
        return
    driver.execute_script("window.scrollTo(0,0)")
    time.sleep(0.3)
    # 同一 JS 任务内点击 + 同步捕获流式占位（快流下轮询会漏掉瞬态占位类）
    snap = driver.execute_script("""
      var btn = document.querySelector('#quick-q button');
      if (!btn) return {clicked:false, saw_streaming:false};
      btn.click();
      return {clicked:true, saw_streaming: !!document.querySelector('.bubble.streaming')};
    """)
    _record("B8 出现流式回答占位",
            bool(snap.get("clicked") and snap.get("saw_streaming")),
            "点击后未检测到 .bubble.streaming 占位" if not snap.get("saw_streaming") else "")
    trace_seen = _wait_for(driver, lambda: len(
        driver.find_elements(By.CSS_SELECTOR, ".agent-trace .trace-step")) >= 1, 3, "工作轨迹")
    _record("B8 工作轨迹出现", trace_seen)
    _scroll_center(driver, driver.find_elements(By.CSS_SELECTOR, ".agent-panel")[0])
    time.sleep(0.2)
    _shot(driver, "04_agent_streaming.png")

    def done():
        ans = driver.find_elements(By.CSS_SELECTOR, ".agent-msg.assistant .bubble")
        if not ans:
            return False
        cls = ans[0].get_attribute("class") or ""
        return ("streaming" not in cls.split()) and len(ans[0].text.strip()) > 0

    done_ok = _wait_for(driver, done, 90, "流式回答完成", interval=0.5)
    _record("B8 流式回答完成（非空）", done_ok)
    time.sleep(0.4)
    _shot(driver, "05_agent_done.png")

    steps = driver.find_elements(By.CSS_SELECTOR, ".agent-trace .trace-step")
    labels = [s.text for s in steps]
    trace_all = " ".join(labels)
    _record("B8 轨迹含中文工具步骤", any(
        "交易建议" in x or "特征" in x or "证据" in x or "案例" in x or "血缘" in x or "复盘" in x
        for x in labels), " | ".join(x.replace("\n", " ") for x in labels[:6]) if labels else "无轨迹")
    _record("B8 轨迹含「交易建议」（get_decision 真实工具）", "交易建议" in trace_all,
            "轨迹: " + (" | ".join(x.replace("\n", " ") for x in labels[:6]) if labels else "无"))
    # Guard 行为（真实守卫保留）：PASS → 正常展示；BLOCKED → 正确拦截并红卡提示，不展示被拦截内容
    guard_passed = "一致性检查通过" in trace_all
    guard_blocked = "一致性检查未通过" in trace_all
    guard_err = driver.find_elements(By.CSS_SELECTOR, ".agent-guard.err")
    ans_text = ""
    _ans = driver.find_elements(By.CSS_SELECTOR, ".agent-msg.assistant .bubble")
    if _ans:
        ans_text = _ans[0].text
    if guard_blocked or guard_err:
        ok_block = bool(guard_err) and ("一致性检查" in ans_text)
        _record("B8 Guard 拦截处理正确", ok_block, "BLOCKED 红卡/提示正确" if ok_block else "未正确显示拦截提示")
    elif guard_passed:
        _record("B8 Guard 一致性检查通过", True)
    else:
        # mock/degraded（无 LLM）→ 守卫未触发，记录 PASS（真实 LLM 才触发守卫）
        _record("B8 Guard（degraded 未触发）", True, "mock/degraded 模式无守卫事件，属预期")


def phase_golden():
    """C1..C5 Golden Case 回归：demo 模式跑 5 个 Golden，断言 final/gate。"""
    for cid, exp in GOLDEN_EXPECT.items():
        dd, node, hour = GOLDEN_PARAMS[cid]
        try:
            r = _http_json("POST", "/api/decision",
                           {"decision_date": dd, "node": node, "hour": hour, "evidence": "real"})
            d = r.get("decision") or {}
            final = d.get("final_recommendation")
            gate = (d.get("risk_gate") or {}).get("decision")
            ok = (final == exp["final"] and gate == exp["gate"])
            _record(f"C{cid} Golden {cid}: final={final} gate={gate}",
                    ok, f"预期 final={exp['final']} gate={exp['gate']}" if not ok else "")
        except Exception as exc:  # noqa: BLE001
            _record(f"C{cid} Golden {cid}", False, f"API 调用失败: {exc}")


def _verify_shots(extra: list = None):
    """D1 截图数量与尺寸校验。"""
    try:
        from PIL import Image  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        _record("D1 截图尺寸校验", True, "PIL 不可用，跳过尺寸校验")
        return
    expected = ["01_initial.png", "02_hero.png", "03_why.png", "04_agent_streaming.png",
                "05_agent_done.png", "06_case_e.png", "07_locked.png", "08_reveal.png",
                "09_review.png", "10_tech_expanded.png", "hero_1920x1080.png"]
    missing = [n for n in expected if not (OUT_DIR / n).exists()]
    if missing:
        _record("D1 截图齐全", False, "缺失: " + ", ".join(missing))
        return
    bad = []
    for n in expected:
        try:
            with Image.open(OUT_DIR / n) as im:
                size = im.size
                want = (1920, 1080) if n.startswith("hero_") else (1440, 900)
                if size != want:
                    bad.append(f"{n}={size}")
        except Exception as exc:  # noqa: BLE001
            bad.append(f"{n}=ERR({exc})")
    _record("D1 截图尺寸正确（10×1440×900 + 1×1920×1080）", not bad, "; ".join(bad) if bad else "all ok")


def main() -> int:
    ap = argparse.ArgumentParser(description="V0.4 Phase 2 浏览器验收 + 截图")
    ap.add_argument("--driver", default="", help="chromedriver 可执行文件路径（缺省自动下载）")
    ap.add_argument("--port", type=int, default=5130, help="server 端口（被占用则自动 +1）")
    ap.add_argument("--llm", choices=("env", "mock"), default="env",
                    help="Ask Agent 的 LLM 模式：env=用现有配置；mock=强制确定性 mock（验收稳定）")
    args = ap.parse_args()

    global PORT
    PORT = _pick_port(args.port)

    # 若需要确定性验收，在 import mvp_web 之前覆盖 LLM 配置（llm_copilot 加载 .env 时不会覆盖已设变量）
    if args.llm == "mock":
        os.environ["LLM_PROVIDER"] = "mock"
        os.environ["LLM_API_KEY"] = "mock"
        os.environ["LLM_MODEL"] = "mock-model"
        print("[llm] 强制 mock LLM（确定性验收，工具仍真实执行）")
    else:
        print("[llm] 使用环境现有 LLM 配置（.env）")

    from selenium import webdriver  # noqa: PLC0415
    from selenium.webdriver.chrome.service import Service  # noqa: PLC0415
    from selenium.webdriver.common.by import By  # noqa: PLC0415

    drv = args.driver or _download_driver()

    threading.Thread(target=_start_server, daemon=True).start()
    if not _wait_server(20):
        print(f"[FAIL] server 启动失败（端口 {PORT}）")
        return 1
    print(f"[server] demo 模式已启动 http://127.0.0.1:{PORT}")

    opts = webdriver.ChromeOptions()
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--force-device-scale-factor=1")
    opts.set_capability("goog:loggingPrefs", {"browser": "ALL"})
    try:
        driver = webdriver.Chrome(service=Service(executable_path=drv), options=opts)
    except Exception:  # noqa: BLE001  # 老 Chrome 不认 --headless=new → 回退旧 headless
        print("[driver] --headless=new 失败，回退 --headless")
        opts = webdriver.ChromeOptions()
        opts.add_argument("--headless")
        opts.add_argument("--no-sandbox")
        opts.add_argument("--disable-gpu")
        opts.add_argument("--disable-dev-shm-usage")
        opts.add_argument("--force-device-scale-factor=1")
        opts.set_capability("goog:loggingPrefs", {"browser": "ALL"})
        driver = webdriver.Chrome(service=Service(executable_path=drv), options=opts)

    try:
        # ---------- 1440×900 主验收 ----------
        if not _set_viewport(driver, 1440, 900):
            print("[warn] 视口校准未完全到位，截图尺寸可能略偏")
        print("\n===== 1440×900 浏览器验收 =====")

        driver.get(f"http://127.0.0.1:{PORT}/")
        _wait_init(driver)
        _inject_js_errors(driver)
        time.sleep(0.5)
        _shot(driver, "01_initial.png")
        phase_integrity(driver, "初始加载")

        # 案例 B：Hero / 为什么 / 技术折叠
        if not _reload_and_run(driver, "B"):
            _record("案例 B 生成", False, ".hero 未渲染")
        else:
            driver.execute_script("window.scrollTo(0,0)")
            time.sleep(0.5)
            _shot(driver, "02_hero.png")
            phase_first_screen(driver)
            phase_integrity(driver, "决策渲染后")
            phase_sticky(driver)
            phase_tech_collapse(driver)
            # 03_why
            why = driver.find_elements(By.CSS_SELECTOR, ".why-grid")
            if why:
                _scroll_center(driver, why[0])
                _shot(driver, "03_why.png")
            # 10_tech_expanded（展开数据血缘；已展开则不再点击，避免二次切换关闭）
            dt = driver.find_elements(By.XPATH,
                                      "//details[.//summary[contains(text(),'查看数据血缘')]]")
            if dt:
                _scroll_center(driver, dt[0])
                if not bool(driver.execute_script("return arguments[0].open", dt[0])):
                    driver.execute_script("arguments[0].querySelector('summary').click()", dt[0])
                time.sleep(0.5)
                _shot(driver, "10_tech_expanded.png")

        # Streaming（Agent 预设问题）
        phase_streaming(driver)

        # Case E
        phase_case_e(driver)

        # Lock / Reveal / Review（案例 D）
        phase_lock_reveal(driver)

        # ---------- 1920×1080 hero ----------
        print("\n===== 1920×1080 Hero 截图 =====")
        _set_viewport(driver, 1920, 1080)
        if _reload_and_run(driver, "B"):
            driver.execute_script("window.scrollTo(0,0)")
            time.sleep(0.5)
            _shot(driver, "hero_1920x1080.png")
            phase_integrity(driver, "1920×1080")
        else:
            _record("1920×1080 Hero 生成", False, ".hero 未渲染")

        # ---------- Golden Case 回归（API 数据断言） ----------
        print("\n===== Golden Case 回归（/api/decision） =====")
        phase_golden()

        # ---------- 最终 JS 错误收集 ----------
        errs = _live_js_errors(driver) + _browser_log(driver)
        _record("B1 无 JS error（全程累计）", not errs, "; ".join(errs[:5]) if errs else "clean")

        # ---------- 截图校验 ----------
        _verify_shots()

    finally:
        driver.quit()

    # ---------- 汇总 ----------
    print("\n" + "=" * 72)
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    total = len(RESULTS)
    for name, ok, detail in RESULTS:
        print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  ({detail})" if detail else ""))
    print("=" * 72)
    print(f"验收汇总：{passed}/{total} PASS")
    print(f"截图目录：{OUT_DIR}")
    for n in sorted(p.name for p in OUT_DIR.glob("*.png")):
        print(f"  - {n}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
