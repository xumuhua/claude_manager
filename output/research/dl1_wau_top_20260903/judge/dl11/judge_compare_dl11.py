#!/usr/bin/env python3
# D-L1.1 判卷比对脚本（judge 工件）——三案契约钉值 × RTL 仿真日志逐点比对
# + trans 期望值独立机算（bankrot 模型 × (bank r+b, row 2+r) 几何 × rail 升序拼装）
# 交叉验证契约钉值本身（不只信钉值、不只信 coder 自检）。
import json, re, sys

CONTRACT = "../claude_manager/output/research/dl1_wau_top_20260903/../../../../../chip_design_ir/examples_vnext/wau_top/contract.ir"
import os
CONTRACT = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "chip_design_ir", "examples_vnext", "wau_top", "contract.ir"))

with open(CONTRACT) as f:
    c = json.load(f)

cases = {cs["id"]: cs for cs in c["contract_cases"]}
ok_all, n_all = True, 0

def parse_events(logpath):
    evs = []
    for line in open(logpath):
        p = line.split()
        if not p: continue
        if p[0] == "O":
            evs.append(("data_out", int(p[1]), p[2].lower(), p[3].lower()))
        elif p[0] == "R":
            evs.append(("rack_out", int(p[1]), int(p[2]), None))
    return evs

def strip0x(s): return s[2:] if s.startswith("0x") else s

# ---------- 独立机算 trans 期望（bankrot × 几何） ----------
def bankrot(bank, row):
    b = [(16*bank + j + row) & 0xff for j in range(16)]
    b[0], b[1] = bank, row
    return bytes(b)

def be(bs):
    """Verilog %x 大端串口径：byte0 在串尾"""
    return bs[::-1].hex()

def trans_expect():
    """beat b rail r 读 (bank r+b, row 2+r)；rail 升序拼 128B；strb 全 1；rack 192"""
    outs = []
    for beat in range(2):
        data = be(b"".join(bankrot(r + beat, 2 + r) for r in range(8)))
        outs.append((data, "ff"*16))
    return outs, [192]

exp_trans, rack_trans = trans_expect()
# 交叉验证：机算结果 vs 契约钉值
for i, e in enumerate(cases["c_trans_e2e_diagonal"]["expect"]["sequence"]):
    if e["port"] == "data_out":
        assert strip0x(e["payload"]["data"].lower()) == exp_trans[i][0], f"钉值{i}.data 与机算不符"
        assert strip0x(e["payload"]["strb"].lower()) == exp_trans[i][1], f"钉值{i}.strb 与机算不符"
    else:
        assert e["payload"]["uop_mid"] == rack_trans[0], "钉值 rack 与机算不符"
print("[机算交叉验证] trans 期望（bankrot×几何×rail 升序）≡ 契约钉值：3/3 自洽")

# ---------- 三案钉值 × 仿真日志比对（按端口分组——DR4/D-L1 既定口径：
# 契约考核点为端口内逐点 + rack 序，未考核跨端口交错序；b2b 案 rack(160) 于
# UOP0 数据出线后即回执属自然语义，见 verdict_dl1.md §2 登记裁定） ----------
for cid, logf in [("c_xuop_b2b_data", "sim_b2b.judge.log"),
                  ("c_single_window_edge", "sim_edge.judge.log"),
                  ("c_trans_e2e_diagonal", "sim_trans.judge.log")]:
    exp = cases[cid]["expect"]["sequence"]
    got = parse_events(logf)
    exp_data = [(strip0x(e["payload"]["data"].lower()),
                 strip0x(e["payload"]["strb"].lower())) for e in exp if e["port"] == "data_out"]
    exp_rack = [e["payload"]["uop_mid"] for e in exp if e["port"] == "rack_out"]
    got_data = [(g2, g3) for (gp, _, g2, g3) in got if gp == "data_out"]
    got_rack = [g2 for (gp, _, g2, _) in got if gp == "rack_out"]
    ok, n = True, 0
    if len(got_data) != len(exp_data) or len(got_rack) != len(exp_rack):
        print(f"[{cid}] 事务数不等：data {len(got_data)}/{len(exp_data)} rack {len(got_rack)}/{len(exp_rack)}")
        ok = False
    for i, ((gd, gs), (ed, es)) in enumerate(zip(got_data, exp_data)):
        n += 2
        if gd != ed or gs != es:
            ok = False; print(f"  [{cid}] data[{i}].data/strb MISMATCH：{gd[:32]}… vs {ed[:32]}…")
    for i, (gm, em) in enumerate(zip(got_rack, exp_rack)):
        n += 1
        if gm != em:
            ok = False; print(f"  [{cid}] rack[{i}] MISMATCH：{gm} vs {em}")
    print(f"[{cid}] 钉值×日志逐点比对（端口分组）：{'%d/%d MATCH' % (n, n) if ok else 'FAIL'}"
          + (f"  [跨端口交错序：data 内序/rack 内序均严格一致——D-L1 既定裁定，非考核点]" if ok and cid == "c_xuop_b2b_data" else ""))
    ok_all &= ok; n_all += n

print(f"\n=== 三案判定：{'ALL MATCH (%d/%d)' % (n_all, n_all) if ok_all else 'FAIL'} ===")
sys.exit(0 if ok_all else 1)
