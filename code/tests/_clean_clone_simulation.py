# -*- coding: utf-8 -*-
"""
clean_clone_simulation.py —— V0.3.1.1 Clean Clone 模拟验证（Agent D 验收临时脚本）

任务 2：临时把 code/data 隐藏/重命名（模拟 clean clone 无研究数据），验证：
  1. prepare_mvp.py 自动解析为 DEMO（退出码 0）
  2. DecisionService（DEMO）跑通 5 个 Golden Case，结果与 FULL 一致
  3. 恢复 code/data，git 状态/文件内容不丢
若 code/data 被占用无法重命名 → 回退"指向空 data_dir 的 data_mode 解析"模拟并说明。
"""
import hashlib
import os
import subprocess
import sys
import tempfile
import shutil
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

DATA_DIR = REPO_ROOT / "code" / "data"
DATA_BAK = REPO_ROOT / "code" / "data_cleanclone_bak"

#: 子进程统一 UTF-8（项目约定：mvp_web / 演示均以 -X utf8 运行；GBK 管道无法编码 ⚠ emoji）
SUB_ENV = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}

GOLDEN = [
    {"id": "B",  "dd": "2026-07-16", "node": "CONTROLX_1_N001", "hour": 3},
    {"id": "C1", "dd": "2026-07-08", "node": "CONTROLX_1_N001", "hour": 2},
    {"id": "C2", "dd": "2026-07-10", "node": "SNLNDRO_1_N001",  "hour": 10},
    {"id": "D",  "dd": "2026-07-20", "node": "SNLNDRO_1_N001",  "hour": 20},
    {"id": "E",  "dd": "2026-07-08", "node": "CONTROLX_1_N001", "hour": 2},
]

SUMMARY = []


def report(msg: str) -> None:
    print(msg)
    SUMMARY.append(msg)


def run_demo_svc(data_dir: Path):
    """用 DEMO 数据跑 5 个 Golden Case，返回 {case_id: dec}。"""
    from code.decision_service import DecisionService, StaticEvidenceAdapter
    svc = DecisionService(data_dir=data_dir, evidence_adapter=StaticEvidenceAdapter([]))
    assert svc.data_mode == "DEMO", f"data_mode 应为 DEMO，实际 {svc.data_mode}"
    out = {}
    for g in GOLDEN:
        out[g["id"]] = svc.run_decision(g["dd"], g["node"], g["hour"], reveal=True)
    return out


def run_full_baseline():
    from code.decision_service import DecisionService, StaticEvidenceAdapter
    svc = DecisionService(data_dir=DATA_DIR, evidence_adapter=StaticEvidenceAdapter([]))
    assert svc.data_mode == "FULL", f"data_mode 应为 FULL，实际 {svc.data_mode}"
    out = {}
    for g in GOLDEN:
        out[g["id"]] = svc.run_decision(g["dd"], g["node"], g["hour"], reveal=True)
    return out


def pick(d):
    mo = d.get("model_output", {})
    rg = d.get("risk_gate", {})
    pt = d.get("post_trade", {}) or {}
    return {
        "final": d.get("final_recommendation"),
        "gate": rg.get("decision"),
        "expected_return": mo.get("expected_return"),
        "signal": mo.get("model_signal_strength"),
        "pnl": pt.get("pnl"),
        "actual_da": pt.get("actual_da"),
        "actual_rtpd": pt.get("actual_rtpd"),
    }


def compare(full, demo):
    bad = []
    for cid in full:
        f, d = pick(full[cid]), pick(demo[cid])
        for k in ("final", "gate", "expected_return", "signal", "pnl", "actual_da", "actual_rtpd"):
            fv, dv = f[k], d[k]
            if isinstance(fv, float) and isinstance(dv, float):
                ok = abs(fv - dv) <= 1e-6
            else:
                ok = fv == dv
            if not ok:
                bad.append(f"{cid}.{k}: FULL={fv!r} DEMO={dv!r}")
    return bad


def dir_digest(root: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(root.rglob("*")):
        if p.is_file():
            h.update(p.relative_to(root).as_posix().encode("utf-8"))
            h.update(p.read_bytes())
    return h.hexdigest()


def main() -> int:
    print("=" * 72)
    print("V0.3.1.1 Clean Clone 模拟验证")
    print("=" * 72)
    if not DATA_DIR.exists():
        report("code/data 不存在（本身就是 clean clone 状态）→ 直接验证 DEMO 即可。")
    else:
        # 0) 记录改名前的目录摘要（用于恢复校验）
        digest_before = dir_digest(DATA_DIR)

        # 1) FULL 基线（改名之前，避免受 DEMO 影响）
        print("\n[1] 计算 FULL 基线（5 个 Golden Case）...")
        full = run_full_baseline()
        print("    FULL baseline OK")

        # 2) 尝试隐藏 code/data
        renamed = False
        try:
            os.rename(str(DATA_DIR), str(DATA_BAK))
            renamed = True
            print(f"\n[2] code/data → code/data_cleanclone_bak（模拟 clean clone 成功）")
        except Exception as exc:  # noqa: BLE001
            print(f"\n[2] code/data 重命名失败（{type(exc).__name__}: {exc}）→ 回退方案")
            if DATA_BAK.exists():
                shutil.rmtree(DATA_BAK, ignore_errors=True)

        if renamed:
            try:
                # 3) prepare_mvp.py 自动 → DEMO
                print("\n[3] python prepare_mvp.py（自动探测）...")
                r = subprocess.run([sys.executable, "-X", "utf8", "prepare_mvp.py"],
                                   cwd=str(REPO_ROOT), capture_output=True, text=True,
                                   timeout=180, env=SUB_ENV)
                out_text = (r.stdout or "") + (r.stderr or "")
                is_demo = "DATA MODE = DEMO" in out_text and r.returncode == 0
                report(f"    prepare_mvp 退出码={r.returncode}，auto→DEMO: {'PASS' if is_demo else 'FAIL'}")
                print("    " + (out_text.splitlines()[0] if out_text else "(no output)"))

                # 4) DecisionService（DEMO）跑 5 个 Golden Case
                print("\n[4] DecisionService(DEMO) 跑 5 个 Golden Case ...")
                demo = run_demo_svc(REPO_ROOT / "demo_artifacts")
                bad = compare(full, demo)
                if bad:
                    report("    DEMO vs FULL 一致性: FAIL（" + "; ".join(bad) + "）")
                else:
                    report("    DEMO vs FULL 一致性: PASS（5/5，final/gate/数值/PnL 全部一致）")

                # 5) mvp_demo.py 以 DEMO 模式可导入（不联网；验证 CLI 渲染层 + 1 个
                #    Golden Case 决策链——决策链唯一来源 DecisionService）
                print("\n[5] mvp_demo.py 模块在 DEMO 模式可导入并跑通决策链（不联网）...")
                code = (
                    "import sys; sys.path.insert(0, r'%s'); "
                    "from code.decision_service import DecisionService, StaticEvidenceAdapter; "
                    "import mvp_demo; "  # CLI 渲染层可导入（无第二套决策链）
                    "from data_mode import resolve_data_mode; "
                    "info = resolve_data_mode(); assert info.mode == 'DEMO'; "
                    "svc = DecisionService(data_dir=r'%s', evidence_adapter=StaticEvidenceAdapter([])); "
                    "d = svc.run_decision('2026-07-16', 'CONTROLX_1_N001', 3); "
                    "print('DEMO_MODE:', info.mode, '| final:', d['final_recommendation'])"
                ) % (REPO_ROOT, REPO_ROOT / "demo_artifacts")
                r2 = subprocess.run([sys.executable, "-X", "utf8", "-c", code],
                                    cwd=str(REPO_ROOT), capture_output=True, text=True,
                                    timeout=180, env=SUB_ENV)
                print("    " + (r2.stdout or r2.stderr or "").strip())
                mvp_demo_ok = r2.returncode == 0 and "DEMO_MODE: DEMO" in (r2.stdout or "")
                report(f"    mvp_demo 模块 DEMO 模式跑通（不联网）: {'PASS' if mvp_demo_ok else 'FAIL'}")
            finally:
                # 6) 恢复
                print("\n[6] 恢复 code/data ...")
                os.rename(str(DATA_BAK), str(DATA_DIR))
                digest_after = dir_digest(DATA_DIR)
                restored_ok = (digest_before == digest_after)
                report(f"    恢复完成；目录摘要一致: {'PASS' if restored_ok else 'FAIL'}")
                if not restored_ok:
                    print("    !! 摘要不一致，请立即人工检查 code/data（勿覆盖）。")
        else:
            # ---- 回退方案：指向空 data_dir 的 data_mode 解析 ----
            print("\n[FALLBACK] 用'指向空 data_dir 的 data_mode 解析'模拟 clean clone：")
            with tempfile.TemporaryDirectory() as tmp:
                clone = Path(tmp)
                shutil.copytree(REPO_ROOT / "demo_artifacts", clone / "demo_artifacts")
                (clone / "code" / "data").mkdir(parents=True, exist_ok=True)   # 空 data_dir
                from data_mode import MODE_DEMO, resolve_data_mode
                info = resolve_data_mode(repo_root=clone)
                report(f"    空 code/data + demo_artifacts → data_mode = {info.mode}（期望 DEMO）")
                demo = run_demo_svc(clone / "demo_artifacts")
                bad = compare(full, demo)
                report("    回退方案 DEMO vs FULL 一致性: "
                       + ("PASS（5/5）" if not bad else "FAIL（" + "; ".join(bad) + "）"))

    print("\n" + "=" * 72)
    print("Clean Clone 模拟验证摘要")
    for s in SUMMARY:
        print("  - " + s)
    print("=" * 72)
    return 0


if __name__ == "__main__":
    sys.exit(main())
