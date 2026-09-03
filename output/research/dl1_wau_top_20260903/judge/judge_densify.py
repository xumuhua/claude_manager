#!/usr/bin/env python3
"""判卷独立复算 v3：core 两案 data/strb 期望值（G-vNext-13 钉值反推权威口径）。

口径（hlc_check README G-vNext-13 + selfcheck densify_abs 登记的反推口径，
判卷方独立实现）：
  single 致密化：
    - 组界绝对 4B 对齐；致密窗 rot>0 自组 ⌊A/4⌋−1 起（回挂一组前缀）共
      ⌈(vbytes+4)/4⌉ 组，rot=0 自 ⌊A/4⌋ 起 ⌈vbytes/4⌉ 组
    - parity 按绝对组号：偶组顺次→低半区 byte0 起，奇组顺次→高半区 byte64 起
    - strb：各半区自 byte0（/byte64）起连续置 n 位，n = 该半区内落在
      [A, A+vbytes) 的字节数（前缀组入窗但不计位）
  multi 线性段：slot 顺次拼接，strb 低 vbytes 位置 1。
"""
import json

CONTRACT = "/home/glmdev/dl1_judge_work/chip_design_ir/examples_vnext/wau_top/contract.ir"

def hexbytes(h):
    h = h.lower().removeprefix("0x")
    return [int(h[i:i+2], 16) for i in range(len(h)-2, -1, -2)]

def densify_single(slots, A, vbytes):
    rot = A % 16
    w = [b for s in slots for b in s]
    lin0 = A - rot                              # 工作线 byte0 的绝对地址
    if rot > 0:
        off = 4*(rot//4) - 4                    # 窗在工作线内偏移（回挂一组）
        ng = -(-(vbytes + 4)//4)
    else:
        off, ng = 0, -(-vbytes//4)
    g0 = (lin0 + off)//4                        # 窗首绝对组号
    data = [0]*128
    lo_n = hi_n = 0                             # strb 有效计数
    pos_lo = pos_hi = 0                         # 数据摆放游标（前缀组也占位）
    for k in range(ng):
        g = g0 + k
        seg = w[off+4*k: off+4*k+4]
        for j, b in enumerate(seg):
            valid = A <= lin0 + off + 4*k + j < A + vbytes
            if g % 2:
                if pos_hi < 64:
                    data[64+pos_hi] = b; pos_hi += 1
                    if valid: hi_n += 1
            else:
                if pos_lo < 64:
                    data[pos_lo] = b; pos_lo += 1
                    if valid: lo_n += 1
    strb = ((1 << lo_n) - 1) | (((1 << hi_n) - 1) << 64)
    return data, strb, lo_n, hi_n

def to_data_hex(data):
    return "0x" + "".join(f"{b:02x}" for b in reversed(data))

def multi_linear(slots, vbytes):
    data = ([b for s in slots for b in s] + [0]*128)[:128]
    strb = (1 << vbytes) - 1
    return data, strb

def main():
    cases = json.load(open(CONTRACT))["contract_cases"]
    ok = True
    def chk(name, data, strb, payload):
        nonlocal ok
        gd, gs = to_data_hex(data), f"0x{strb:032x}"
        for k, got, exp in (("data", gd, payload["data"].lower()),
                            ("strb", gs, payload["strb"].lower())):
            m = got == exp
            ok &= m
            print(f"  {name}.{k}: {'MATCH' if m else 'FAIL'}")
            if not m: print(f"    exp={exp}\n    got={got}")

    c = next(x for x in cases if x["id"] == "c_single_window_edge")
    slots = [hexbytes(s["payload"]["data"]) for s in c["stimulus"]["sequence"]
             if s["port"].startswith("bank_data")]
    print("== c_single_window_edge（single 20B @0x11C, rot=12）==")
    d, s, lo, hi = densify_single(slots, 0x11C, 20)
    print(f"  （独立机算：低半区有效 {lo}B @byte0..{lo-1}，高半区有效 {hi}B @byte64..{64+hi-1}）")
    chk("edge", d, s, c["expect"]["sequence"][0]["payload"])

    c2 = next(x for x in cases if x["id"] == "c_xuop_b2b_data")
    bd = {}
    for s in c2["stimulus"]["sequence"]:
        if s["port"].startswith("bank_data"):
            b = int(s["port"][s["port"].index("[")+1:s["port"].index("]")])
            bd.setdefault(b, []).append(hexbytes(s["payload"]["data"]))
    print("== c_xuop_b2b_data beatA（single 48B @0x100, rot=0）==")
    d, s, lo, hi = densify_single([bd[16].pop(0), bd[17].pop(0), bd[18].pop(0)], 0x100, 48)
    chk("beatA", d, s, c2["expect"]["sequence"][0]["payload"])
    for nm, banks in (("beatB", range(0, 8)), ("beatC", range(8, 16))):
        print(f"== c_xuop_b2b_data {nm}（multi 线性段）==")
        d, s = multi_linear([bd[b].pop(0) for b in banks], 128)
        chk(nm, d, s, c2["expect"]["sequence"][1 if nm == "beatB" else 2]["payload"])
    print("\n独立复算结论:", "ALL MATCH（契约钉值可推导）" if ok else "存在 FAIL")

main()
