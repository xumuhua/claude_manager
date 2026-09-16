#!/usr/bin/env python3
"""SUPERVISOR-1 本机 mock 自验：三功能各跑通。

- 功能①信道监听→任务执行：mock hub deliver @coder 消息 → 任务 prompt 三段式点火
- 功能②定时复盘：review_time 设为 3 秒后 → 复盘 prompt 七步点火
- 功能③并发闸≤3+排队+任务优先复盘让路：max=2 场景下人为构造
  复盘在跑+槽空 → 任务插队先于排队复盘点火
- 六件套：骨架自动初始化 + 热读取集进 prompt 核对

假 claude 落调用日志（kind/prompt 摘要/起止时刻），全部经日志断言，不依赖 ps 全表
（本环境 ps 全表与 Popen 子进程不同步，见 busfix1 教训）。
"""
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent
PY = sys.executable
HUB_PORT = 18791
TOKEN = "selftest-token"

results = []  # (name, ok, detail)


def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" —— {detail}" if detail else ""))


def write_mock_claude(d: Path) -> Path:
    """假 claude：解析 prompt 打标签，按 SLEEP 环境/文件名延时，落一行 JSON 调用记录。"""
    p = d / "mock_claude.sh"
    p.write_text("""#!/bin/bash
# 自验用假 claude：$1=--dangerously-skip-permissions $2=-p $3=prompt
prompt="${@: -1}"
kind="unknown"
case "$prompt" in
  *"任务执行 prompt"*) kind="job" ;;
  *"每日复盘 prompt"*) kind="review" ;;
esac
sleep_s=0
case "$prompt" in
  *"__SLEEP2__"*) sleep_s=2 ;;
  *"__SLEEP6__"*) sleep_s=6 ;;
esac
start=$(date +%s.%N)
sleep "$sleep_s"
end=$(date +%s.%N)
# 提取关键标记供断言
has_read_set=no; has_group_log=no; has_archive=no; has_steps=no
case "$prompt" in *"第一步：读自己的落盘信息"*) has_read_set=yes ;; esac
case "$prompt" in *"第二步：读 bus 群消息"*) has_group_log=yes ;; esac
case "$prompt" in *"收场归档"*) has_archive=yes ;; esac
case "$prompt" in *"复盘七步"*) has_steps=yes ;; esac
trig=""
case "$prompt" in *"__TRIG1__"*) trig="__TRIG1__" ;; esac
python3 - "$FAKECLAUDE_LOG" "$kind" "$start" "$end" "$has_read_set" "$has_group_log" "$has_archive" "$has_steps" "$trig" <<'EOF'
import json, sys
log, kind, start, end, rs, gl, ar, st, trig = sys.argv[1:]
with open(log, "a", encoding="utf-8") as f:
    f.write(json.dumps({"kind": kind, "start": float(start), "end": float(end),
                        "read_set": rs, "group_log": gl, "archive": ar,
                        "steps": st, "trig": trig}) + "\\n")
EOF
echo "FAKECLAUDE-DONE kind=$kind"
""", encoding="utf-8")
    p.chmod(0o755)
    return p


def make_config(d: Path, fake: Path, review_time: str, max_conc: int,
                expert_dir: Path, extra=None) -> Path:
    cfg = {
        "hub_url": f"http://127.0.0.1:{HUB_PORT}",
        "token": "env:SUP_SELFTEST_TOKEN",
        "agent_name": "coder",
        "conversations": ["grp_mp"],
        "trigger_groups": ["grp_mp"],
        "review_groups": ["grp_mp"],
        "review_time": review_time,
        "review_preempt_delay": 1,
        "expert_dir": str(expert_dir),
        "archive_dir": str(d / "archive"),
        "work_dir": str(d),
        "concurrency": {"max": max_conc, "job_timeout": 30, "review_timeout": 30},
        "claude": {"cmd": str(fake), "args": [], "max_chars": 60000, "ctx_keep": 8},
        "job_template": str(BASE / "prompts" / "job.md"),
        "review_template": str(BASE / "prompts" / "review.md"),
        "history_hot_days": 2,
    }
    if extra:
        cfg.update(extra)
    p = d / "config.json"
    p.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def start_supervisor(cfg_path: Path, fake_log: Path, env_extra=None) -> subprocess.Popen:
    env = dict(os.environ)
    env["SUP_SELFTEST_TOKEN"] = TOKEN
    env["FAKECLAUDE_LOG"] = str(fake_log)
    if env_extra:
        env.update(env_extra)
    out = open(cfg_path.parent / "supervisor_stdout.log", "w", encoding="utf-8")
    return subprocess.Popen([PY, str(BASE / "supervisor.py"), str(cfg_path)],
                            env=env, stdout=out, stderr=subprocess.STDOUT)


def read_calls(fake_log: Path):
    if not fake_log.exists():
        return []
    return [json.loads(l) for l in fake_log.read_text(encoding="utf-8").splitlines() if l.strip()]


def wait_calls(fake_log: Path, n: int, timeout: float) -> list:
    t0 = time.time()
    while time.time() - t0 < timeout:
        calls = read_calls(fake_log)
        if len(calls) >= n:
            return calls
        time.sleep(0.2)
    return read_calls(fake_log)


def scenario_A(root: Path):
    """功能①+②：review_time=3秒后；hub 1秒后 deliver @coder 任务消息。"""
    print("=== 场景A：功能①信道触发 + 功能②定时复盘 ===")
    d = root / "A"; d.mkdir(parents=True)
    fake = write_mock_claude(d)
    fake_log = d / "fake_calls.jsonl"
    expert_dir = d / "expert"

    msgs = [{"delay": 1.0, "msg": {
        "seq": 1, "ts": "2026-09-16T00:00:01Z", "from": "yifei",
        "conversation_id": "grp_mp", "type": "text",
        "body": "【派单】@coder 测试任务 __TRIG1__", "mentions": ["coder"]}}]
    spec = d / "msgs.json"; spec.write_text(json.dumps(msgs), encoding="utf-8")

    rt = time.strftime("%H:%M", time.localtime(time.time() + 3))
    cfg = make_config(d, fake, rt, max_conc=3, expert_dir=expert_dir,
                      extra={"review_delay_seconds": 3})
    hub = subprocess.Popen([PY, str(BASE / "mock_hub.py"), str(HUB_PORT), str(spec)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(0.8)
    sup = start_supervisor(cfg, fake_log)
    try:
        calls = wait_calls(fake_log, 2, timeout=130)
    finally:
        sup.terminate(); hub.terminate()
        for p, name in ((sup, "supervisor"), (hub, "mock_hub")):
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill(); p.wait(timeout=5)

    kinds = sorted(c["kind"] for c in calls)
    check("A1 三功能中①②点火", kinds == ["job", "review"],
          f"点火 {len(calls)} 次 kinds={kinds}")
    job = next((c for c in calls if c["kind"] == "job"), {})
    check("A2 任务 prompt 三段式",
          job.get("read_set") == "yes" and job.get("group_log") == "yes"
          and job.get("archive") == "yes" and job.get("trig") == "__TRIG1__",
          f"read_set={job.get('read_set')} group_log={job.get('group_log')} "
          f"archive={job.get('archive')} trig={job.get('trig')}")
    review = next((c for c in calls if c["kind"] == "review"), {})
    check("A3 复盘 prompt 七步+读落盘",
          review.get("steps") == "yes" and review.get("read_set") == "yes",
          f"steps={review.get('steps')} read_set={review.get('read_set')}")
    # 六件套骨架
    six = ["identity.md", "state.yaml", "knowledge.md", "dir_map.yaml"]
    missing = [f for f in six if not (expert_dir / f).exists()]
    check("A4 六件套骨架初始化", not missing and (expert_dir / "history").is_dir(),
          f"missing={missing}")


def scenario_B(root: Path):
    """功能③并发闸：max=2，复盘先点（review_time=1秒后，SLEEP6），
    随后 3 个任务消息 → 在跑 2 个期间第 3 个排队；复盘在跑槽空出时任务插队。"""
    print("=== 场景B：功能③并发闸≤N+排队+任务优先复盘让路 ===")
    d = root / "B"; d.mkdir(parents=True)
    fake = write_mock_claude(d)
    fake_log = d / "fake_calls.jsonl"
    expert_dir = d / "expert"

    # 时间线：t=1 复盘点火(SLEEP6)；t=2/2.5/3 三个任务(SLEEP2)
    msgs = [
        {"delay": 2.0, "msg": {"seq": 11, "ts": "2026-09-16T00:00:11Z", "from": "yifei",
         "conversation_id": "grp_mp", "type": "text",
         "body": "任务1 __SLEEP2__", "mentions": ["coder"]}},
        {"delay": 0.5, "msg": {"seq": 12, "ts": "2026-09-16T00:00:12Z", "from": "yifei",
         "conversation_id": "grp_mp", "type": "text",
         "body": "任务2 __SLEEP2__", "mentions": ["coder"]}},
        {"delay": 0.5, "msg": {"seq": 13, "ts": "2026-09-16T00:00:13Z", "from": "gege",
         "conversation_id": "grp_mp", "type": "text",
         "body": "任务3 __SLEEP2__", "mentions": ["coder"]}},
    ]
    spec = d / "msgs.json"; spec.write_text(json.dumps(msgs), encoding="utf-8")

    rt = time.strftime("%H:%M", time.localtime(time.time() + 61))  # 复盘不由定时器点
    cfg = make_config(d, fake, rt, max_conc=2, expert_dir=expert_dir)
    hub = subprocess.Popen([PY, str(BASE / "mock_hub.py"), str(HUB_PORT), str(spec)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(0.8)
    sup = start_supervisor(cfg, fake_log)
    try:
        calls = wait_calls(fake_log, 3, timeout=40)
    finally:
        sup.terminate(); hub.terminate()
        for p in (sup, hub):
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill(); p.wait(timeout=5)

    jobs = sorted([c for c in calls if c["kind"] == "job"], key=lambda c: c["start"])
    check("B1 三个任务全部点火", len(jobs) == 3, f"jobs={len(jobs)} calls={len(calls)}")
    if len(jobs) == 3:
        # max=2：任务1、2 先跑（各 2s），任务3 必须等任务1或2结束后才点火
        overlap12 = jobs[0]["start"] < jobs[1]["end"] and jobs[1]["start"] < jobs[0]["end"]
        third_after = jobs[2]["start"] >= min(jobs[0]["end"], jobs[1]["end"]) - 0.05
        check("B2 并发闸≤2：前两个重叠跑", overlap12,
              f"j0=[{jobs[0]['start']:.1f},{jobs[0]['end']:.1f}] "
              f"j1=[{jobs[1]['start']:.1f},{jobs[1]['end']:.1f}]")
        check("B3 第3个排队等空槽", third_after,
              f"j2.start={jobs[2]['start']:.1f} 前两个最早结束="
              f"{min(jobs[0]['end'], jobs[1]['end']):.1f}")


def scenario_C(root: Path):
    """让路：max=1，复盘1(SLEEP6)在跑独占槽位；队首=复盘2、队中=任务X。
    复盘1结束槽空 → 队首复盘2 应让路，任务X 先点（拍板②：任务优先复盘让路）。
    白盒注入：直接操 Gate 队列构造形态，点火仍走同一 Gate 代码路径。"""
    print("=== 场景C：任务优先复盘让路（队首复盘 vs 队中任务） ===")
    d = root / "C"; d.mkdir(parents=True)
    fake = write_mock_claude(d)
    fake_log = d / "fake_calls.jsonl"
    expert_dir = d / "expert"
    os.environ["FAKECLAUDE_LOG"] = str(fake_log)

    rt = time.strftime("%H:%M", time.localtime(time.time() + 61))  # 复盘不由定时器点
    cfg = make_config(d, fake, rt, max_conc=1, expert_dir=expert_dir)

    # 白盒注入：在同进程内起 supervisor 组件，直接操 Gate 队列
    sys.path.insert(0, str(BASE))
    import supervisor as sup_mod
    import asyncio

    # 复盘2 标记 R2、任务X 标记 JOBWIN（打进了 prompt 字符串，mock 未提取，
    # 用 kind 序列即可判定让路）；复盘1 SLEEP6 独占槽位。
    async def run_gate_test():
        raw = json.loads(cfg.read_text(encoding="utf-8"))
        os.environ["SUP_SELFTEST_TOKEN"] = TOKEN
        cfg2 = sup_mod.load_config(cfg)
        sup_mod.ensure_bootstrap(cfg2)
        gate = sup_mod.Gate(cfg2, sup_mod.Launcher(None))
        disp = asyncio.ensure_future(gate.run())
        # 复盘1（SLEEP6）在跑
        await gate.submit("review", lambda: ("每日复盘 prompt 复盘七步 第一步：读自己的落盘信息 __SLEEP6__", 30), {"label": "复盘1"})
        await asyncio.sleep(0.5)  # 复盘1 已点火
        # 队首=复盘2，队中=任务（构造让路形态）
        await gate.submit("review", lambda: ("每日复盘 prompt 复盘七步 第一步：读自己的落盘信息 __SLEEP2__ R2", 30), {"label": "复盘2"})
        await gate.submit("job", lambda: ("任务执行 prompt 第一步：读自己的落盘信息 第二步：读 bus 群消息 收场归档 __SLEEP2__ JOBWIN", 30), {"label": "任务X"})
        # 等复盘1结束（6s）+让路窗口+后续两个跑完
        t0 = time.time()
        while time.time() - t0 < 30:
            if len(read_calls(fake_log)) >= 3:
                break
            await asyncio.sleep(0.3)
        disp.cancel()
        return read_calls(fake_log)

    calls = asyncio.run(run_gate_test())
    by_start = sorted(calls, key=lambda c: c["start"])
    kinds_seq = [c["kind"] for c in by_start]
    check("C1 三个会话全部点火", len(calls) == 3, f"kinds={kinds_seq}")
    if len(calls) == 3:
        check("C2 复盘1先点（max=1 独占）", by_start[0]["kind"] == "review",
              f"first={by_start[0]['kind']}")
        # 复盘1结束后：队首复盘2 应让路，任务X 先点
        check("C3 任务优先复盘让路：第二个点火的是任务",
              by_start[1]["kind"] == "job",
              f"second={by_start[1]['kind']}（预期 job，复盘让路）")
        check("C4 让路后复盘最后补位", by_start[2]["kind"] == "review",
              f"third={by_start[2]['kind']}")


def main():
    root = Path(tempfile.mkdtemp(prefix="sup_selftest_"))
    print(f"自验工作目录: {root}")
    try:
        scenario_A(root)
        scenario_B(root)
        scenario_C(root)
    finally:
        print(f"（保留现场供核查：{root}；确认后可 rm -rf）")
    print("=== 汇总 ===")
    failed = [r for r in results if not r[1]]
    print(f"{len(results) - len(failed)}/{len(results)} PASS")
    if failed:
        for name, _, detail in failed:
            print(f"  FAIL: {name} {detail}")
    sys.exit(1 if failed else 0)


main()
