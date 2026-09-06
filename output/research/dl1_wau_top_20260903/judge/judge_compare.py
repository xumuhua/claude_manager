#!/usr/bin/env python3
"""D-L1 判卷 · 契约案逐点比对（contract.ir 钉值 × RTL 仿真日志）。

口径（沿 DR4 l1_check 先例）：
  - data_out 端口：期望拍数内逐字节钉值（data 256hex + strb 32hex），顺序敏感
  - rack_out 端口：mid 序列全等
  - 跨端口相对时序：非考核点（契约考核①data 逐字节②rack 序③边界不串）
  - 额外：事务数恰等（多一拍/少一拍/多一笔 rack 均 FAIL）
"""
import json, sys

CONTRACT = "/home/glmdev/dl1_judge_work/chip_design_ir/examples_vnext/wau_top/contract.ir"

def parse_log(path):
    o, r = [], []
    for line in open(path):
        p = line.split()
        if not p:
            continue
        if p[0] == "O":
            o.append({"data": p[2].lower(), "strb": p[3].lower()})
        elif p[0] == "R":
            r.append(int(p[2]))
    return o, r

def expect_of(cid):
    cases = json.load(open(CONTRACT))["contract_cases"]
    c = next(x for x in cases if x["id"] == cid)
    o, r = [], []
    for ev in c["expect"]["sequence"]:
        if ev["port"] == "data_out":
            o.append({"data": ev["payload"]["data"].lower().removeprefix("0x"),
                      "strb": ev["payload"]["strb"].lower().removeprefix("0x")})
        elif ev["port"] == "rack_out":
            r.append(ev["payload"]["uop_mid"])
    return o, r

def main():
    ok_all, n_pt = True, 0
    for cid, log in [("c_xuop_b2b_data", "sim_b2b.judge.log"),
                     ("c_single_window_edge", "sim_edge.judge.log")]:
        eo, er = expect_of(cid)
        ao, ar = parse_log(log)
        print(f"== {cid} ==")
        # data_out
        if len(ao) != len(eo):
            print(f"  FAIL data 拍数: 期望 {len(eo)} 实测 {len(ao)}"); ok_all = False
        for i, (e, a) in enumerate(zip(eo, ao)):
            for k in ("data", "strb"):
                n_pt += 1
                if e[k] != a[k]:
                    ok_all = False
                    print(f"  FAIL data[{i}].{k}:")
                    print(f"    exp={e[k]}")
                    print(f"    got={a[k]}")
                    # 首个差异字节定位
                    for j, (x, y) in enumerate(zip(e[k], a[k])):
                        if x != y:
                            print(f"    首差 @char{j} (byte {len(e[k])*4-(j+1)*4//4} 区) exp'{x}' got'{y}'")
                            break
                else:
                    print(f"  OK  data[{i}].{k} ({len(a[k])} hex) MATCH")
        # rack_out
        n_pt += 1
        if ar == er:
            print(f"  OK  rack 序 {ar} MATCH")
        else:
            ok_all = False
            print(f"  FAIL rack 序: 期望 {er} 实测 {ar}")
    print(f"\n总计 {n_pt} 比对点：{'ALL MATCH' if ok_all else '存在 FAIL'}")
    sys.exit(0 if ok_all else 1)

main()
