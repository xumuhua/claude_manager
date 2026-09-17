#!/usr/bin/env python3
"""SUPERVISOR-1 本机 mock 自验：三功能各跑通。

- 功能①信道监听→任务执行：mock hub deliver @coder 消息 → 任务 prompt 三段式点火
- 功能②定时复盘：review_time 设为 3 秒后 → 复盘 prompt 七步点火
- 功能③并发闸≤3+排队+任务优先复盘让路：max=2 场景下人为构造
  复盘在跑+槽空 → 任务插队先于排队复盘点火
- 六件套：骨架自动初始化 + 热读取集进 prompt 核对

SUPERVISOR-3 追加（场景 F/G）：
- F1-F4 知会消息不点火：echo 回执/兜底回执/schedule 回执/复盘摘要，且 supervisor
  stdout 留 [filter] 日志行；F5 任务书派单正常点火；F6 自然语言派单正常点火
- G1 trigger_filter=false 热关回退：echo 消息恢复点火；G2 兜底回执 mentions 空

SUPERVISOR-4 追加（场景 H）：
- H1 claude 引擎 spawn 参数逐字节同现状（argv/cwd/stdin 继承）回归
- H2 codex 参数构造（{outfile} 替换+args 末尾 '-'，prompt 走 stdin，cwd=workdir）
- H3 全链路 engine=codex：mock codex stdin→outfile→✅回执含 [TASK_DONE] 产出
- H4 codex 码0但 outfile 空 → ⚠️档回执；H5 点火日志带 engine=codex 标注

假 claude 落调用日志（kind/prompt 摘要/起止时刻），全部经日志断言，不依赖 ps 全表
（本环境 ps 全表与 Popen 子进程不同步，见 busfix1 教训）。
"""
import json
import os
import re
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
  *"定时任务执行 prompt"*) kind="sched_job" ;;
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
case "$prompt" in *"__SCHED1__"*) trig="__SCHED1__" ;; esac
# 触发消息 body 原文回收（SUPERVISOR-3 F3：区分两条同批 job 的触发源）
bodies=""
case "$prompt" in *"__NLTEST__"*) bodies="__NLTEST__" ;; esac
# P0 三档回执用例控制：__RC0_MARK__=退出0+自报标记；__RC0_NOMARK__=退出0无标记；
# __RC7__=退出码7+stderr。标记行内容含场景标记供回执内容断言。
mark=""
case "$prompt" in
  *"__RC0_MARK__"*) mark="[TASK_DONE] 产出: 自验产出X __RC0_MARK__" ;;
esac
# 区分序：任务A/B 同批 prompt 差异在 seq——B(seq22) 无标记档
case "$prompt" in *"seq 22"*) mark="" ;; esac
# 定时任务自验：__SCHED1__ 退出0+自报标记（回执内容带场景标记）
case "$prompt" in
  *"__SCHED1__"*) mark="[TASK_DONE] 产出: 自验定时产出Y __SCHED1__" ;;
esac
rc=0
case "$prompt" in *"__RC7__"*) rc=7 ;; esac
python3 - "$FAKECLAUDE_LOG" "$kind" "$start" "$end" "$has_read_set" "$has_group_log" "$has_archive" "$has_steps" "$trig" "$bodies" <<'EOF'
import json, sys
log, kind, start, end, rs, gl, ar, st, trig, bodies = sys.argv[1:]
with open(log, "a", encoding="utf-8") as f:
    f.write(json.dumps({"kind": kind, "start": float(start), "end": float(end),
                        "read_set": rs, "group_log": gl, "archive": ar,
                        "steps": st, "trig": trig, "bodies": bodies}) + "\\n")
EOF
if [ "$rc" != "0" ]; then
  echo "FAKECLAUDE-ERROR boom __RC7__" >&2
  exit "$rc"
fi
[ -n "$mark" ] && echo "$mark"
echo "FAKECLAUDE-DONE kind=$kind"
""", encoding="utf-8")
    p.chmod(0o755)
    return p


def make_config(d: Path, fake: Path, review_time: str, max_conc: int,
                expert_dir: Path, extra=None) -> Path:
    cfg = {
        "hub_url": f"http://127.0.0.1:{HUB_PORT}",
        "hub_http_url": f"http://127.0.0.1:{HUB_PORT + 1}",  # mock HTTP=WS+1
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


def wait_seconds(fake_log: Path, n: int, seconds: float) -> list:
    """固定窗口读点火记录（负向断言用：不能提前返回，必须等满窗口）。"""
    time.sleep(seconds)
    return read_calls(fake_log)


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


def scenario_D(root: Path):
    """P0 on_job_done 三档兜底回执：✅(0+标记)/⚠️(0 无标记)/❌(退出码非0)。
    mock hub 同端口收 POST /messages（MOCK_HUB_POSTS 落盘），断言：
    每档回执进触发群 grp_mp、mentions 含触发者、文案档位正确、复盘不发回执。"""
    print("=== 场景D：P0 on_job_done 三档兜底回执（✅/⚠️/❌） ===")
    d = root / "D"; d.mkdir(parents=True)
    fake = write_mock_claude(d)
    fake_log = d / "fake_calls.jsonl"
    posts_log = d / "hub_posts.jsonl"
    expert_dir = d / "expert"

    msgs = [
        {"delay": 1.0, "msg": {"seq": 21, "ts": "2026-09-16T00:00:21Z",
         "from": "yifei", "conversation_id": "grp_mp", "type": "text",
         "body": "任务A __RC0_MARK__", "mentions": ["coder"]}},
        {"delay": 1.0, "msg": {"seq": 22, "ts": "2026-09-16T00:00:22Z",
         "from": "gege", "conversation_id": "grp_mp", "type": "text",
         "body": "任务B __RC0_NOMARK__", "mentions": ["coder"]}},
        {"delay": 1.0, "msg": {"seq": 23, "ts": "2026-09-16T00:00:23Z",
         "from": "yifei", "conversation_id": "grp_mp", "type": "text",
         "body": "任务C __RC7__", "mentions": ["coder"]}},
    ]
    spec = d / "msgs.json"; spec.write_text(json.dumps(msgs), encoding="utf-8")

    rt = time.strftime("%H:%M", time.localtime(time.time() + 61))  # 复盘不点火
    cfg = make_config(d, fake, rt, max_conc=3, expert_dir=expert_dir)
    hub_env = dict(os.environ); hub_env["MOCK_HUB_POSTS"] = str(posts_log)
    hub = subprocess.Popen([PY, str(BASE / "mock_hub.py"), str(HUB_PORT), str(spec)],
                           env=hub_env,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(0.8)
    sup = start_supervisor(cfg, fake_log)
    try:
        calls = wait_calls(fake_log, 3, timeout=40)
        # 回执在 job 结束后异步 POST，给发信留窗
        t0 = time.time()
        posts = []
        while time.time() - t0 < 20:
            if posts_log.exists():
                posts = [json.loads(l) for l in
                         posts_log.read_text(encoding="utf-8").splitlines()
                         if l.strip()]
                if len(posts) >= 3:
                    break
            time.sleep(0.3)
    finally:
        sup.terminate(); hub.terminate()
        for p in (sup, hub):
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill(); p.wait(timeout=5)

    check("D1 三个任务全部点火", len(calls) >= 3, f"calls={len(calls)}")
    check("D2 三条回执全部入库", len(posts) >= 3, f"posts={len(posts)}")
    bodies = [p["msg"]["body"] for p in posts]
    convs = {p["msg"]["conversation_id"] for p in posts}
    check("D3 回执全部进触发群 grp_mp", convs == {"grp_mp"}, f"convs={convs}")
    check("D4 回执七字段齐备",
          all(set(p["msg"]) >= {"msg_id", "conversation_id", "from", "mentions",
                                "type", "body", "reply_to"} for p in posts),
          f"keys={sorted(posts[0]['msg'].keys()) if posts else '无'}")
    # 按 jid 分档：#1=✅ #2=⚠️ #3=❌（job_seq 按入队序，msgs 顺序入队）
    def find(emoji):
        return next((b for b in bodies if b.startswith(emoji)), None)
    ok_b, warn_b, err_b = find("✅"), find("⚠️"), find("❌")
    check("D5 ✅档：码0+标记含耗时+产出",
          ok_b is not None and "耗时" in ok_b and "自验产出X" in ok_b,
          f"body={ok_b}")
    check("D6 ⚠️档：码0 无标记",
          warn_b is not None and "完成但未自报产出" in warn_b,
          f"body={warn_b}")
    check("D7 ❌档：退出码7+stderr 摘要",
          err_b is not None and "退出码7" in err_b and "boom" in err_b,
          f"body={err_b}")
    # mentions 含触发者：✅(#1,seq21,yifei)/⚠️(#2,seq22,gege)/❌(#3,seq23,yifei)
    def mentions_of(prefix):
        p = next((p for p in posts if p["msg"]["body"].startswith(prefix)), None)
        return p["msg"]["mentions"] if p else None
    # SUPERVISOR-3 修法②：三档兜底回执 mentions 一律清空（原为含触发者）
    check("D8 mentions 一律清空（SUPERVISOR-3 修法②）",
          mentions_of("✅") == [] and mentions_of("⚠️") == []
          and mentions_of("❌") == [],
          f"✅→{mentions_of('✅')} ⚠️→{mentions_of('⚠️')} ❌→{mentions_of('❌')}")

def scenario_E(root: Path):
    """SUPERVISOR-2 定时任务表 schedules：
    E1 schedule_delay_seconds 秒级到点点火（kind=job 走三段式 sched prompt）
    E2 prompt 含读落盘/读群消息/prompt_file 正文标记 __SCHED1__
    E3 兜底回执发 target_group、mentions 空、body 带 label 前缀+✅档
    E4 weekdays 过滤：不匹配项不点火
    E5 重启防重复点火：fired 已记当天 → 不二次点火
    E6 无 schedules 字段：行为与首版一致（A/B/C/D 全部不挂 schedule_timer，PASS 即证）
    """
    print("=== 场景E：SUPERVISOR-2 定时任务表 schedules ===")
    d = root / "E"; d.mkdir(parents=True)
    fake = write_mock_claude(d)
    fake_log = d / "fake_calls.jsonl"
    posts_log = d / "hub_posts.jsonl"
    expert_dir = d / "expert"
    pf = d / "daily.md"
    pf.write_text("每日定时任务正文：干 __SCHED1__ 这件事并收场", encoding="utf-8")

    # 今天 cron 口径周日数；不匹配项用 (today+3)%7 保证今天必不中
    sys.path.insert(0, str(BASE))
    import supervisor as sup_mod
    import datetime as _dt
    today_cron = (_dt.date.today().weekday() + 1) % 7
    off_cron = (today_cron + 3) % 7

    rt = time.strftime("%H:%M", time.localtime(time.time() + 3600))  # 复盘不点火
    cfg = make_config(d, fake, rt, max_conc=2, expert_dir=expert_dir,
                      extra={
                          "schedule_delay_seconds": 2,  # 每分第2秒点火
                          "schedules": [
                              {"time": "09:30", "weekdays": "*",
                               "prompt_file": str(pf), "kind": "job",
                               "target_group": "grp_mp", "label": "日报E"},
                              {"time": "10:00", "weekdays": str(off_cron),
                               "prompt_file": str(pf), "kind": "job",
                               "target_group": "grp_mp", "label": "永不点E"},
                          ]})
    hub_env = dict(os.environ); hub_env["MOCK_HUB_POSTS"] = str(posts_log)
    spec = d / "msgs.json"; spec.write_text("[]", encoding="utf-8")
    hub = subprocess.Popen([PY, str(BASE / "mock_hub.py"), str(HUB_PORT), str(spec)],
                           env=hub_env,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(0.8)
    sup = start_supervisor(cfg, fake_log)
    try:
        # 等每分第2秒到点（最坏 60s 一轮）
        calls = wait_calls(fake_log, 1, timeout=75)
        t0 = time.time()
        posts = []
        while time.time() - t0 < 15:
            if posts_log.exists():
                posts = [json.loads(l) for l in
                         posts_log.read_text(encoding="utf-8").splitlines()
                         if l.strip()]
                if len(posts) >= 1:
                    break
            time.sleep(0.3)
        # 再等一个完整分钟，确认"永不点E"没点、且"日报E"当日不二次点火
        time.sleep(0.5)
        t1 = time.time()
        while time.time() - t1 < 62:
            if len([c for c in read_calls(fake_log) if c["kind"] == "sched_job"]) > 1:
                break  # 防重失效会快速暴露
            time.sleep(0.5)
        calls2 = read_calls(fake_log)
    finally:
        sup.terminate(); hub.terminate()
        for p in (sup, hub):
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill(); p.wait(timeout=5)

    sched = [c for c in calls2 if c["kind"] == "sched_job"]
    check("E1 定时任务到点点火 kind=sched_job", len(sched) == 1,
          f"sched_job 点火 {len(sched)} 次（全日calls={len(calls2)}）")
    if sched:
        c = sched[0]
        check("E2 三段式骨架+prompt_file 正文进 prompt",
              c.get("read_set") == "yes" and c.get("group_log") == "yes"
              and c.get("archive") == "yes" and c.get("trig") == "__SCHED1__",
              f"read_set={c.get('read_set')} group_log={c.get('group_log')} "
              f"archive={c.get('archive')} trig={c.get('trig')}")
    check("E4 weekdays 不匹配项不点火", len(sched) <= 1,
          f"总 sched_job={len(sched)}（日报E 当日防重已记，永不点E weekdays 排除）")
    sp = [p for p in posts if "[日报E]" in p["msg"]["body"]]
    check("E3 兜底回执发 target_group mentions 空",
          len(sp) == 1 and sp[0]["msg"]["conversation_id"] == "grp_mp"
          and sp[0]["msg"]["mentions"] == []
          and sp[0]["msg"]["body"].startswith("[日报E] ✅"),
          f"posts={[(p['msg']['body'][:40], p['msg']['mentions']) for p in posts]}")

    # E5 白盒：fired 已记当天 → _schedule_due 不重复点火（同生产路径：weekdays+time 全过滤）
    os.environ["SUP_SELFTEST_TOKEN"] = TOKEN
    cfg2 = sup_mod.load_config(cfg)
    st = sup_mod.StateStore(expert_dir / "state.json")
    sup2 = sup_mod.Supervisor(cfg2)
    now = _dt.datetime.now()
    s_due = [s for s in cfg2["schedules"]
             if sup2._schedule_due(s, now)
             and st.fired.get(s["_fire_key"]) != now.date().isoformat()]
    check("E5 重启后 fired 防重复点火", not s_due,
          f"当日到点未点项={[(s['label']) for s in s_due]}（期望空：日报E fired 已记，"
          f"永不点E weekdays 排除）")

def scenario_F(root: Path):
    """SUPERVISOR-3 修法①：闸二知会消息识别（哥哥 9/17 拍板，宁严勿宽）。
    四条知会格式（echo/兜底回执/schedule 回执/复盘摘要）带 @coder → 不点火，
    且 supervisor stdout 每条留 [filter] 日志；两条真派单（任务书/自然语言）
    → 正常点火。"""
    print("=== 场景F：SUPERVISOR-3 触发过滤（知会不点火/真派单点火） ===")
    d = root / "F"; d.mkdir(parents=True)
    fake = write_mock_claude(d)
    fake_log = d / "fake_calls.jsonl"
    expert_dir = d / "expert"

    def m(seq, body, sender="aicorp", mention=True):
        return {"delay": 0.6, "msg": {
            "seq": seq, "ts": f"2026-09-17T00:00:{seq:02d}Z", "from": sender,
            "conversation_id": "grp_mp", "type": "text",
            "body": body, "mentions": (["coder"] if mention else [])}}
    msgs = [
        m(30, "（占位：防订阅握手竞态丢首条 deliver，此条无 @不触发）",
          "yifei", mention=False),
        m(31, "[echo:aicorp] 确认收讫（seq 30）"),                       # 知会·echo
        m(32, "✅ #9 完成（耗时100s）产出：blabla"),                     # 知会·兜底✅
        m(33, "⚠️ #10 完成但未自报产出（耗时42s）：请人工看一眼"),        # 知会·兜底⚠️
        m(34, "❌ #11 异常（耗时5s）：退出码7"),                         # 知会·兜底❌
        m(35, "[日报] ✅ #3 完成（耗时60s）产出：X"),                    # 知会·schedule
        m(36, "【复盘 0917】今日三事……"),                                # 知会·复盘摘要
        m(37, "【每日复盘 0917】今日三事……"),                            # 知会·每日复盘
        m(38, "SUPERVISOR-9 任务书：修复 filter，自验后部署 __TRIG1__",
          "yifei"),                                                     # 真派单·任务书
        m(39, "哥哥让问问你昨天那个模块进展 __NLTEST__", "gege"),        # 真派单·自然语言
    ]
    spec = d / "msgs.json"; spec.write_text(json.dumps(msgs), encoding="utf-8")

    rt = time.strftime("%H:%M", time.localtime(time.time() + 3600))  # 复盘不点火
    cfg = make_config(d, fake, rt, max_conc=3, expert_dir=expert_dir)
    hub = subprocess.Popen([PY, str(BASE / "mock_hub.py"), str(HUB_PORT), str(spec)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(0.8)
    sup = start_supervisor(cfg, fake_log)
    try:
        # 9 条 × 0.6s deliver + job 收尾，留 15s 固定窗口（负向断言须等满）；
        # 若 hub 在订阅握手前开闸会丢首条 deliver，等满后点火数不够则重跑一轮
        for attempt in range(3):
            calls = wait_seconds(fake_log, 2, seconds=15)
            stdout_log = (d / "supervisor_stdout.log").read_text(encoding="utf-8")
            if len(calls) >= 2:
                break
            if attempt < 2:
                print(f"（F 场景首条 deliver 疑似丢失，重跑第 {attempt + 2} 轮）")
                sup.terminate(); hub.terminate()
                for p in (sup, hub):
                    try:
                        p.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        p.kill(); p.wait(timeout=5)
                fake_log.unlink(missing_ok=True)
                (d / "supervisor_stdout.log").unlink(missing_ok=True)
                hub = subprocess.Popen(
                    [PY, str(BASE / "mock_hub.py"), str(HUB_PORT), str(spec)],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                time.sleep(0.8)
                sup = start_supervisor(cfg, fake_log)
    finally:
        sup.terminate(); hub.terminate()
        for p in (sup, hub):
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill(); p.wait(timeout=5)

    jobs = [c for c in calls if c["kind"] == "job"]
    check("F1 仅 2 次点火（四条知会全部拦下，两条真派单放行）",
          len(calls) == 2 and len(jobs) == 2,
          f"calls={len(calls)} kinds={sorted(c['kind'] for c in calls)}")
    trigs = {c.get("trig") for c in jobs}
    check("F2 任务书派单正常点火（__TRIG1__）", "__TRIG1__" in trigs,
          f"trigs={trigs}")
    # 自然语言派单：prompt 带触发消息 body 原文（__NLTEST__ 为独有标记）
    nl_hit = any("__NLTEST__" in (c.get("bodies") or "") for c in jobs)
    check("F3 自然语言派单正常点火（__NLTEST__ 进 prompt）", nl_hit,
          f"jobs={[(c['kind'], c['trig'], (c.get('bodies') or '')[:20]) for c in jobs]}")
    filter_lines = [l for l in stdout_log.splitlines() if "[filter]" in l]
    hit_seqs = {int(re.search(r"seq=(\d+)", l).group(1))
                for l in filter_lines if re.search(r"seq=(\d+)", l)}
    check("F4 七条知会各有 [filter] 日志行（seq 31-37）",
          hit_seqs == {31, 32, 33, 34, 35, 36, 37},
          f"hit_seqs={sorted(hit_seqs)} 样例={filter_lines[0].strip() if filter_lines else '无'}")


def scenario_G(root: Path):
    """SUPERVISOR-3：G1 trigger_filter=false 热关回退（echo 恢复点火）；
    G2 修法② on_job_done 兜底回执 mentions 清空。"""
    print("=== 场景G：trigger_filter 热关 + 兜底回执 mentions 空 ===")
    d = root / "G"; d.mkdir(parents=True)
    fake = write_mock_claude(d)
    fake_log = d / "fake_calls.jsonl"
    posts_log = d / "hub_posts.jsonl"
    expert_dir = d / "expert"

    msgs = [
        {"delay": 1.0, "msg": {"seq": 41, "ts": "2026-09-17T00:00:41Z",
         "from": "aicorp", "conversation_id": "grp_mp", "type": "text",
         "body": "[echo:aicorp] 确认收讫 __RC0_MARK__", "mentions": ["coder"]}},
    ]
    spec = d / "msgs.json"; spec.write_text(json.dumps(msgs), encoding="utf-8")

    rt = time.strftime("%H:%M", time.localtime(time.time() + 3600))
    cfg = make_config(d, fake, rt, max_conc=2, expert_dir=expert_dir,
                      extra={"trigger_filter": False})  # 热关回退
    hub_env = dict(os.environ); hub_env["MOCK_HUB_POSTS"] = str(posts_log)
    hub = subprocess.Popen([PY, str(BASE / "mock_hub.py"), str(HUB_PORT), str(spec)],
                           env=hub_env,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(0.8)
    sup = start_supervisor(cfg, fake_log)
    try:
        calls = wait_calls(fake_log, 1, timeout=20)
        t0 = time.time()
        posts = []
        while time.time() - t0 < 15:
            if posts_log.exists():
                posts = [json.loads(l) for l in
                         posts_log.read_text(encoding="utf-8").splitlines()
                         if l.strip()]
                if len(posts) >= 1:
                    break
            time.sleep(0.3)
    finally:
        sup.terminate(); hub.terminate()
        for p in (sup, hub):
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill(); p.wait(timeout=5)

    check("G1 trigger_filter=false 时 echo 消息恢复点火（热关回退生效）",
          len(calls) == 1 and calls[0]["kind"] == "job",
          f"calls={len(calls)} kinds={[c['kind'] for c in calls]}")
    check("G2 on_job_done 兜底回执 mentions 清空（修法②）",
          len(posts) == 1 and posts[0]["msg"]["mentions"] == []
          and posts[0]["msg"]["body"].startswith("✅"),
          f"posts={[(p['msg']['body'][:30], p['msg']['mentions']) for p in posts]}")


def write_mock_codex(d: Path) -> Path:
    """假 codex：codex exec --skip-git-repo-check --output-last-message <out> -
    从 stdin 读 prompt，解析标记后把应答写进 --output-last-message 指定文件。
    __CX_MARK__ → 应答含 [TASK_DONE] 标记；__CX_EMPTY__ → 码0但不写 outfile。"""
    p = d / "mock_codex.sh"
    p.write_text("""#!/bin/bash
outfile=""
prev=""
for a in "$@"; do
  if [ "$prev" = "--output-last-message" ]; then outfile="$a"; fi
  prev="$a"
done
prompt="$(cat)"
echo "$outfile" >> "$FAKECODEX_OUTFILES"
echo "$prompt" | grep -q "__CX7__" && { echo "FAKECODEX-ERROR boom" >&2; exit 7; }
if echo "$prompt" | grep -q "__CX_EMPTY__"; then
  echo "FAKECODEX-DONE empty" >&2; exit 0
fi
if [ -n "$outfile" ]; then
  if echo "$prompt" | grep -q "__CX_MARK__"; then
    printf '[TASK_DONE] 产出: codex自验产出 __CX_MARK__\\n' > "$outfile"
  else
    printf 'codex 应答无标记\\n' > "$outfile"
  fi
fi
echo "FAKECODEX-DONE"
""", encoding="utf-8")
    p.chmod(0o755)
    return p


def scenario_H(root: Path):
    """SUPERVISOR-4 应答引擎双支持：
    H1 claude 引擎 spawn 参数构造逐字节同现状（cmd+args+prompt 在 argv、
       cwd=_work_dir、无 stdin），回归零变化；
    H2 codex 引擎参数构造正确（{outfile} 占位符替换+args 末尾 '-'，prompt 走 stdin）；
    H3 全链路 engine=codex：@派单 → mock codex stdin→outfile → ✅回执含标记产出；
    H4 codex 码0但 outfile 空 → ⚠️档回执；
    H5 点火日志带 engine= 标注。"""
    print("=== 场景H：SUPERVISOR-4 双引擎（claude 回归 + codex 通路） ===")
    d = root / "H"; d.mkdir(parents=True)
    sys.path.insert(0, str(BASE))
    import supervisor as sup_mod

    # H1/H2 白盒：直接调 spawn_engine 断言参数构造（cmd 指向 python dump 脚本
    # 拦截 argv/cwd/stdin 落盘；stdin 为空串 = DEVNULL 口径，claude prompt 在 argv）
    expert_dir = d / "expert"
    expert_dir.mkdir(parents=True)
    argv_log = d / "argv.jsonl"
    dump_py = d / "argv_dump.py"
    dump_py.write_text('''
import json, os, sys
data = {"argv": sys.argv[1:], "cwd": os.getcwd(),
        "stdin": None if sys.stdin.isatty() else sys.stdin.read()}
with open(os.environ["FAKEARGV"], "a", encoding="utf-8") as f:
    f.write(json.dumps(data, ensure_ascii=False) + "\\n")
''', encoding="utf-8")
    os.environ["SUP_SELFTEST_TOKEN"] = TOKEN
    os.environ["FAKEARGV"] = str(argv_log)  # Popen 默认继承父进程 env
    os.environ.pop("FAKECODEX_OUTFILES", None)  # 防 H 白盒段泄漏到全链路 mock codex

    cfg_h = {
        "hub_url": f"http://127.0.0.1:{HUB_PORT}",
        "hub_http_url": f"http://127.0.0.1:{HUB_PORT + 1}",
        "token": "env:SUP_SELFTEST_TOKEN",
        "agent_name": "coder",
        "expert_dir": str(expert_dir),
        "work_dir": str(d / "workdir_claude"),
        "claude": {"cmd": PY, "args": [str(dump_py),
                                       "--dangerously-skip-permissions", "-p"]},
    }
    (d / "workdir_claude").mkdir()
    cfgp = d / "cfg_h1.json"
    cfgp.write_text(json.dumps(cfg_h, ensure_ascii=False), encoding="utf-8")
    cfg1 = sup_mod.load_config(cfgp)
    cp = sup_mod.spawn_engine(cfg1, "PROMPT_H1_回归", 30, "h1")
    rec = json.loads(argv_log.read_text(encoding="utf-8").splitlines()[-1])
    # dump.py 记录 sys.argv[1:]（脚本名已剔），即完整 claude args+prompt
    check("H1 claude 引擎参数逐字节同现状（cmd+args+prompt 在 argv，stdin 关闭）",
          rec["argv"] == ["--dangerously-skip-permissions", "-p", "PROMPT_H1_回归"]
          and rec["stdin"] == ""   # DEVNULL → 立即 EOF
          and rec["cwd"] == str(d / "workdir_claude")
          and cp.returncode == 0,
          f"argv={rec['argv']} stdin={rec['stdin']!r} cwd={rec['cwd']}")

    # H2 codex 参数构造：{outfile} 替换 + '-' 收尾 + stdin=prompt + cwd=codex.workdir
    argv_log.unlink(missing_ok=True)
    cfg_h["responder"] = {"engine": "codex"}
    cfg_h["codex"] = {"cmd": PY,
                      "args": [str(dump_py), "exec", "--skip-git-repo-check",
                               "--output-last-message", "{outfile}", "-"],
                      "timeout": 30, "workdir": str(d / "workdir_codex")}
    (d / "workdir_codex").mkdir()
    cfgp.write_text(json.dumps(cfg_h, ensure_ascii=False), encoding="utf-8")
    cfg2 = sup_mod.load_config(cfgp)
    cp2 = sup_mod.spawn_engine(cfg2, "PROMPT_H2_CODEX", 30, "h2")
    rec2 = json.loads(argv_log.read_text(encoding="utf-8").splitlines()[-1])
    a = rec2["argv"]  # dump.py 记录 sys.argv[1:]（脚本名已剔），即完整 codex args
    check("H2 codex 参数构造：{outfile} 替换+args 末尾 '-'，prompt 走 stdin",
          a[:3] == ["exec", "--skip-git-repo-check", "--output-last-message"]
          and a[3].startswith(str(expert_dir / "codex_out"))
          and a[3].endswith(".md")
          and a[4] == "-"
          and rec2["stdin"] == "PROMPT_H2_CODEX"
          and rec2["cwd"] == str(d / "workdir_codex")
          and (expert_dir / "codex_out").is_dir()
          and getattr(cp2, "_empty_out", False) is True,  # 拦截器未写文件→空档
          f"argv={a} cwd={rec2['cwd']} stdin={rec2['stdin']!r} "
          f"empty_out={getattr(cp2, '_empty_out', None)}")

    # H3/H4 全链路：engine=codex + mock codex，@派单两条（MARK→✅ / EMPTY→⚠️）
    fake_codex = write_mock_codex(d)
    fake_log = d / "fake_calls.jsonl"   # codex 不写此日志，仅占位
    posts_log = d / "hub_posts.jsonl"
    outfiles_log = d / "outfiles.log"
    msgs = [
        {"delay": 1.0, "msg": {"seq": 51, "ts": "2026-09-17T00:00:51Z",
         "from": "yifei", "conversation_id": "grp_mp", "type": "text",
         "body": "codex任务A __CX_MARK__", "mentions": ["coder"]}},
        {"delay": 1.0, "msg": {"seq": 52, "ts": "2026-09-17T00:00:52Z",
         "from": "yifei", "conversation_id": "grp_mp", "type": "text",
         "body": "codex任务B __CX_EMPTY__", "mentions": ["coder"]}},
    ]
    spec = d / "msgs.json"; spec.write_text(json.dumps(msgs), encoding="utf-8")
    rt = time.strftime("%H:%M", time.localtime(time.time() + 3600))
    cfg_full = make_config(d, fake_codex, rt, max_conc=2,
                           expert_dir=d / "expert_full",
                           extra={
                               "responder": {"engine": "codex"},
                               "codex": {"cmd": str(fake_codex),
                                         "args": ["exec", "--skip-git-repo-check",
                                                  "--output-last-message",
                                                  "{outfile}", "-"],
                                         "timeout": 30, "workdir": str(d)},
                           })
    hub_env = dict(os.environ); hub_env["MOCK_HUB_POSTS"] = str(posts_log)
    hub = subprocess.Popen([PY, str(BASE / "mock_hub.py"), str(HUB_PORT), str(spec)],
                           env=hub_env,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(0.8)
    sup = start_supervisor(cfg_full, fake_log,
                           env_extra={"FAKECODEX_OUTFILES": str(outfiles_log)})
    try:
        t0 = time.time()
        posts = []
        while time.time() - t0 < 30:
            if posts_log.exists():
                posts = [json.loads(l) for l in
                         posts_log.read_text(encoding="utf-8").splitlines()
                         if l.strip()]
                if len(posts) >= 2:
                    break
            time.sleep(0.3)
    finally:
        sup.terminate(); hub.terminate()
        for p in (sup, hub):
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill(); p.wait(timeout=5)

    bodies = [p["msg"]["body"] for p in posts]
    ok_b = next((b for b in bodies if b.startswith("✅")), None)
    warn_b = next((b for b in bodies if b.startswith("⚠️")), None)
    check("H3 codex 全链路：stdin→outfile→✅回执含 [TASK_DONE] 产出",
          ok_b is not None and "codex自验产出" in ok_b and "__CX_MARK__" in ok_b,
          f"✅={ok_b}")
    check("H4 codex 码0但 outfile 空 → ⚠️档回执",
          warn_b is not None and "应答文件为空" in warn_b,
          f"⚠️={warn_b}")
    stdout_log = (d / "supervisor_stdout.log").read_text(encoding="utf-8")
    eng_lines = [l for l in stdout_log.splitlines()
                 if "点火 #" in l and "engine=codex" in l]
    check("H5 点火日志标注 engine=codex", len(eng_lines) >= 2,
          f"engine=codex 行数={len(eng_lines)} 样例={eng_lines[0].strip() if eng_lines else '无'}")


def main():
    root = Path(tempfile.mkdtemp(prefix="sup_selftest_"))
    print(f"自验工作目录: {root}")
    try:
        scenario_A(root)
        scenario_B(root)
        scenario_C(root)
        scenario_D(root)
        scenario_E(root)
        scenario_F(root)
        scenario_G(root)
        scenario_H(root)
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
