#!/usr/bin/env python3
"""experts_collect.py — MP-DASH1 采集层（manager 机 cron */10 * * * *）。

全队专家状态采集：SSH 并行抓三机（203 六用户 / quant / AWS gpt），写死规则提炼
（不调 LLM，零 quota 零延迟），落 ./experts.json，
再 scp 推送到专家机 mp-backend 数据目录（203 coder@/data/workspace/expert-intercom/mp-backend/）。

卡片字段（任务书 §卡片字段）：
  status       working / idle / stale（claude 进程在但最近 log >2h 无写入）/ offline
  current_task 进程 cmdline 提炼（首行任务书编号 + 启动时间 + 已跑时长）
  recent       最近动作（latest *.log mtime + 文件名）
  today        今日产出计数（~ 与 ~/.eap 下今日 mtime 的 .log/.md 文件数）
  planned      STATE.yaml 提炼（mission_focus + in_progress 前三 + missions next_check ≤24h）
               + crontab 未来 24h 触发项
机器清单（任务书 §依赖）：
  203  = 115.191.75.203  root 单跳（密码 ZHousy@1），专家=aicorp/aitech/aichip/coder/designer/qa
  quant= 115.190.14.181  root，密码 ZHousy@0（注意是 @0 不是 @1），专家用户 claude
  gpt  = 16.176.157.153  ubuntu + ~/manager.pem，专家用户 gpt（codex 进程体系）
"""
import concurrent.futures as cf
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_JSON = os.path.join(HERE, "experts.json")
PUSH_TARGET = "coder@115.191.75.203:/data/workspace/expert-intercom/mp-backend/experts.json"
PUSH_PASS = "ZHousy@1"

SSH_OPTS = ["-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=10",
            "-o", "LogLevel=ERROR"]
# 注意：sshpass 通道禁配 BatchMode=yes（禁交互=密码认证直接拒），
# BatchMode 仅用于 pem 密钥通道，避免意外挂起等待口令。

HOSTS = {
    "h203":  {"kind": "sshpass", "pass": "ZHousy@1", "user": "root",
              "addr": "115.191.75.203",
              "experts": ["aicorp", "aitech", "aichip", "coder", "designer", "qa"]},
    "quant": {"kind": "sshpass", "pass": "ZHousy@0", "user": "root",
              "addr": "115.190.14.181", "experts": ["quant"]},
    "gpt":   {"kind": "pem", "pem": os.path.expanduser("~/manager.pem"),
              "user": "ubuntu", "addr": "16.176.157.153", "experts": ["gpt"]},
}

# 远端探测脚本：逐专家输出 @name 分节，字段行 key: value。
# state:/cron: 段原样吐原文（截断 130/12 行防超长），由本地 Python 解析。
REMOTE_PROBE = r"""#!/bin/bash
export PATH=/usr/local/bin:/usr/bin:/bin
for u in __USERS__; do
  echo "@$u"
  p=$(pgrep -u $u -x claude 2>/dev/null | head -3 | tr '\n' ',' | sed 's/,$//')
  if [ -z "$p" ]; then p=$(pgrep -u $u -x codex 2>/dev/null | head -3 | tr '\n' ',' | sed 's/,$//'); fi
  echo "procs: ${p:-none}"
  first=$(echo "$p" | cut -d, -f1)
  if [ -n "$first" ] && [ "$first" != "none" ]; then
    et=$(ps -o etimes= -p "$first" 2>/dev/null | tr -d ' ')
    lstart=$(ps -o lstart= -p "$first" 2>/dev/null)
    cmd=$(tr '\0' ' ' < /proc/$first/cmdline 2>/dev/null | cut -c1-200)
    echo "etimes: $et"
    echo "lstart: $lstart"
    echo "cmdline: $cmd"
  fi
  echo "state:"
  sed -n '1,130p' /home/$u/STATE.yaml 2>/dev/null || echo "(none)"
  echo "cron:"
  crontab -u $u -l 2>/dev/null | grep -vE '^[[:space:]]*(#|$)' | grep -v '^INTERCOM_TOKEN' | head -12
  echo "recent:"
  { ls -t /home/$u/*.log /home/$u/.eap/*.log /home/$u/logs/*.log 2>/dev/null | head -3; } | \
    xargs -r stat -c '%n|%Y' 2>/dev/null | cut -c1-160
  td=$(ls -t /home/$u/*任务书*.md /home/$u/*_task.md /home/$u/*_task.txt 2>/dev/null | head -1)
  [ -n "$td" ] && echo "taskfile: $(basename "$td")"
  echo "today_count: $(find /home/$u -maxdepth 2 \( -name '*.log' -o -name '*.md' \) -mtime -1 2>/dev/null | wc -l)"
done
"""

GPT_PROBE_EXTRA = r"""#!/bin/bash
export PATH=/usr/local/bin:/usr/bin:/bin
u=gpt
echo "@$u"
p=$(pgrep -u $u -x codex 2>/dev/null | head -3 | tr '\n' ',' | sed 's/,$//')
echo "procs: ${p:-none}"
first=$(echo "$p" | cut -d, -f1)
if [ -n "$first" ] && [ "$first" != "none" ]; then
  echo "etimes: $(ps -o etimes= -p $first 2>/dev/null | tr -d ' ')"
  echo "lstart: $(ps -o lstart= -p $first 2>/dev/null)"
  echo "cmdline: $(tr '\0' ' ' < /proc/$first/cmdline 2>/dev/null | cut -c1-200)"
fi
echo "state:"
sed -n '1,130p' /home/$u/STATE.yaml 2>/dev/null || echo "(none)"
echo "cron:"
crontab -u $u -l 2>/dev/null | grep -vE '^[[:space:]]*(#|$)' | grep -v '^INTERCOM_TOKEN' | head -12
echo "recent:"
{ ls -t /home/$u/*.log /home/$u/*.md /home/$u/relay_work/*.log 2>/dev/null | head -3; } | \
  xargs -r stat -c '%n|%Y' 2>/dev/null | cut -c1-160
echo "today_count: $(find /home/$u -maxdepth 2 \( -name '*.log' -o -name '*.md' \) -mtime -1 2>/dev/null | wc -l)"
"""

TASK_ID_RE = re.compile(r'([A-Z][A-Z0-9]*(?:-[A-Z0-9]+)+[\w.]*|MP-DASH\d+|EAP-\d\w*|D\d[\w.]*)')


def run(cmd, timeout=60, input_text=None):
    # errors="replace"：远端文本含非 UTF-8 字节（如 GBK 残留）不炸采集（任务书容错口径）
    return subprocess.run(cmd, capture_output=True, text=True, errors="replace",
                          timeout=timeout, input=input_text)


def ssh_probe(host):
    """整台机一次 SSH 取回全部专家原始数据。返回 {expert: raw_text} 或 {}。"""
    users = " ".join("claude" if e == "quant" else e for e in host["experts"])
    probe = GPT_PROBE_EXTRA if host["experts"] == ["gpt"] else \
        REMOTE_PROBE.replace("__USERS__", users)
    if host["kind"] == "sshpass":
        cmd = ["sshpass", "-p", host["pass"], "ssh"] + SSH_OPTS + \
              [f'{host["user"]}@{host["addr"]}', "bash -s"]
    else:
        cmd = ["ssh"] + SSH_OPTS + ["-o", "BatchMode=yes", "-i", host["pem"],
              f'{host["user"]}@{host["addr"]}', "bash -s"]
    try:
        r = run(cmd, timeout=50, input_text=probe)
    except Exception:
        return {}
    if r.returncode != 0 and not r.stdout:
        return {}
    out = {}
    cur, buf = None, []
    for line in r.stdout.splitlines():
        if line.startswith("@") and len(line) > 1 and " " not in line:
            if cur:
                out[cur] = "\n".join(buf)
            cur, buf = line[1:], []
        elif cur:
            buf.append(line)
    if cur:
        out[cur] = "\n".join(buf)
    if "claude" in out and "quant" in host["experts"]:
        out["quant"] = out.pop("claude")
    return out


# ---------- 解析 ----------

def parse_sections(raw):
    """把探测文本切成字段 dict + state/cron/recent 三段原文。"""
    d = {"state": "", "cron": "", "recent": ""}
    key, buf = None, []
    def flush():
        nonlocal key, buf
        if key:
            d[key] = "\n".join(buf).strip("\n")
        key, buf = None, []
    for line in raw.splitlines():
        m = re.match(r'^(\w+):\s?(.*)$', line)
        if m and m.group(1) in ("procs", "etimes", "lstart", "cmdline", "state",
                                "cron", "recent", "taskfile", "today_count"):
            flush()
            key = m.group(1)
            if key in ("state", "cron", "recent"):
                buf = []
            else:
                d[key] = m.group(2).strip()
                key = None
        elif key:
            buf.append(line)
    flush()
    return d


def extract_state_planned(state_text):
    """STATE.yaml 写死规则提炼计划工作（容错：缺段/无文件不炸，返回 []）。"""
    if not state_text or state_text.strip() == "(none)":
        return []
    planned = []
    lines = state_text.splitlines()
    section = None
    item_indent = None
    for i, line in enumerate(lines):
        if re.match(r'^\S', line):
            section = line.split(":")[0].strip()
            item_indent = None
            continue
        if section == "mission_focus" or (section is None and "mission_focus" in line):
            pass
        m = re.match(r'^(\s*)-\s', line)
        if section in ("in_progress", "backlog", "blocked") and m:
            item_indent = len(m.group(1))
        if section in ("in_progress", "backlog") and item_indent is not None:
            t = re.match(r'^\s+(title|id|due):\s*"?(.+?)"?\s*$', line)
            if t and len(line) - len(line.lstrip()) > item_indent:
                k, v = t.group(1), t.group(2)
                if k in ("title", "due"):
                    tag = {"in_progress": "进行中", "backlog": "排队"}[section]
                    planned.append(f"{tag}·{v[:60]}")
        if section == "missions":
            t = re.match(r'^\s+(title):\s*"?(.+?)"?\s*$', line)
            if t:
                planned.append(f"使命·{t.group(2)[:60]}")
    mf = re.search(r'^mission_focus:\s*"?(.+?)"?\s*$', state_text, re.M)
    if mf:
        planned.insert(0, "焦点·" + mf.group(1)[:60])
    # 去重保序，最多 6 条
    seen, out = set(), []
    for p in planned:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out[:6]


def cron_next_runs(cron_text, now, horizon_h=24):
    """crontab 未来 24h 触发项（只支持标准五段 + 步长；@reboot 跳过）。"""
    items = []
    for line in cron_text.splitlines():
        line = line.strip()
        if not line or line.startswith("@") or line.startswith("#"):
            continue
        parts = line.split(None, 5)
        if len(parts) < 6:
            continue
        mi, hh, dom, mon, dow, cmdline = parts
        try:
            nexts = _next_cron(mi, hh, dom, mon, dow, now, horizon_h)
        except Exception:
            continue
        if nexts is not None:
            label = _cron_label(cmdline)
            items.append((nexts, f"{nexts.strftime('%H:%M')} {label}"))
    items.sort(key=lambda x: x[0])
    return [s for _, s in items[:4]]


def _field_vals(spec, lo, hi, wrap=None):
    """cron 字段展开为取值集合；不支持则返回 None。"""
    vals = set()
    for part in spec.split(","):
        m = re.match(r'^(\*|\d+(?:-\d+)?)(?:/(\d+))?$', part)
        if not m:
            return None
        rng, step = m.group(1), int(m.group(2) or 1)
        if rng == "*":
            a, b = lo, hi
        elif "-" in rng:
            a, b = map(int, rng.split("-"))
        else:
            a = b = int(rng)
        for v in range(a, b + 1, step):
            if lo <= v <= hi:
                vals.add(wrap(v) if wrap else v)
    return vals


def _next_cron(mi, hh, dom, mon, dow, now, horizon_h):
    mins = _field_vals(mi, 0, 59)
    hrs = _field_vals(hh, 0, 23)
    doms = _field_vals(dom, 1, 31)
    mons = _field_vals(mon, 1, 12)
    # cron dow: 0/7=周日 → python weekday(): 周一=0 … 周日=6
    dows = _field_vals(dow, 0, 7, wrap=lambda v: (v % 7 + 6) % 7)
    if None in (mins, hrs, mons) or (doms is None and dow != "*") or dows is None:
        return None
    t = now.replace(second=0, microsecond=0) + timedelta(minutes=1)
    end = now + timedelta(hours=horizon_h)
    for _ in range(horizon_h * 60 + 2):
        if t > end:
            return None
        if t.minute in mins and t.hour in hrs and t.month in mons:
            dom_ok = doms is None or t.day in doms
            dow_ok = dows is None or t.weekday() in dows
            if dom_ok and dow_ok:
                return t
        t += timedelta(minutes=1)
    return None


def _cron_label(cmdline):
    m = re.search(r'cat\s+\S*/(\w[\w.-]*)\.md', cmdline)
    if m:
        return m.group(1)
    m = re.search(r'([\w.-]+\.(?:sh|py))', cmdline)
    if m:
        return m.group(1)
    if "claude" in cmdline:
        return "claude 会话"
    return cmdline.strip()[:30]


def build_card(name, raw, now):
    d = parse_sections(raw)
    procs = d.get("procs", "none")
    running = procs not in ("", "none")
    # 最近动作
    recent_list = []
    for ln in d.get("recent", "").splitlines():
        if "|" in ln:
            path, ts = ln.rsplit("|", 1)
            try:
                recent_list.append((int(float(ts)), os.path.basename(path)))
            except ValueError:
                pass
    recent_list.sort(reverse=True)
    last_log_ts = recent_list[0][0] if recent_list else None
    if not running:
        status = "idle"
    elif last_log_ts and now.timestamp() - last_log_ts > 2 * 3600:
        status = "stale"
    else:
        status = "working"
    # 当前任务
    current = None
    if running:
        cmdline = d.get("cmdline", "")
        m = TASK_ID_RE.search(cmdline)
        tid = m.group(1) if m else None
        etimes = int(d.get("etimes") or 0)
        dur = f"{etimes // 3600}h{(etimes % 3600) // 60}m" if etimes >= 3600 \
            else f"{etimes // 60}m"
        lstart = d.get("lstart", "").strip()
        if tid:
            tid = re.sub(r'(_任务书)?\.(md|txt)$', '', tid)
        current = {
            "task": tid or (cmdline[:40] if cmdline else "claude 会话"),
            "started": lstart,
            "elapsed": dur,
        }
    if not current and d.get("taskfile"):
        tf = d["taskfile"]
        m = TASK_ID_RE.search(tf)
        current = {"task": (m.group(1) if m else tf), "started": "", "elapsed": "",
                   "note": "最近任务书"}
    # 计划工作
    planned = extract_state_planned(d.get("state", ""))
    planned += ["定时·" + c for c in cron_next_runs(d.get("cron", ""), now)]
    planned = planned[:8]
    return {
        "name": name,
        "status": status,
        "procs": procs if running else "",
        "current_task": current,
        "recent": [{"ts": ts, "desc": fn} for ts, fn in recent_list[:2]],
        "today": {"files_touched": int(d.get("today_count") or 0)},
        "planned": planned,
    }


def offline_card(name, note="采集失败"):
    return {"name": name, "status": "offline", "procs": "",
            "current_task": None, "recent": [], "today": {},
            "planned": [note]}


def main():
    now = datetime.now(timezone(timedelta(hours=8)))  # 全队口径 UTC+8
    results = {}
    with cf.ThreadPoolExecutor(max_workers=3) as ex:
        futs = {ex.submit(ssh_probe, h): key for key, h in HOSTS.items()}
        for fut in cf.as_completed(futs):
            key = futs[fut]
            try:
                results[key] = fut.result()
            except Exception:
                results[key] = {}
    cards = []
    for key, host in HOSTS.items():
        got = results.get(key) or {}
        for e in host["experts"]:
            if e in got:
                try:
                    cards.append(build_card(e, got[e], now))
                except Exception as ex:
                    cards.append(offline_card(e, f"解析异常 {ex}"))
            else:
                cards.append(offline_card(e))
    payload = {
        "version": 1,
        "collected_at": now.isoformat(timespec="seconds"),
        "cards": cards,
    }
    tmp = OUT_JSON + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    os.replace(tmp, OUT_JSON)
    # 推送专家机 mp-backend 数据目录（mv 原子换名，防接口读到半截）
    r = run(["sshpass", "-p", PUSH_PASS, "scp"] + SSH_OPTS +
            [OUT_JSON, PUSH_TARGET + ".tmp"], timeout=40)
    if r.returncode == 0:
        run(["sshpass", "-p", PUSH_PASS, "ssh"] + SSH_OPTS +
            ["coder@115.191.75.203",
             "mv /data/workspace/expert-intercom/mp-backend/experts.json.tmp "
             "/data/workspace/expert-intercom/mp-backend/experts.json"], timeout=20)
        print(f"[collect] ok cards={len(cards)} pushed")
    else:
        print(f"[collect] ok cards={len(cards)} push FAILED: {r.stderr[:200]}",
              file=sys.stderr)


if __name__ == "__main__":
    main()
