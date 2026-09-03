#!/usr/bin/env python3
"""D-R4 L1 契约执行判卷 · 比对（hlc_eval 机算期望 × iverilog 实测日志）。

MATCH 判据（逐点）：
  1. 主 uid 微码事务序列与机算 expect 全等（顺序+端口+src/dst/dim/uid）
  2. c_ooo：F 事务恰一条 = 机算 final{inst_id,err}；且全部落在第 4 个 done 之后
     （no_inst_done_before_all_four）
FAIL 四栏：条款锚 / 期望（机算）/ 实测 / 激励。
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

CASE_ANCHOR = {  # 契约条款锚（contract.ir case id + 考核面）
    0: "contract.ir#c_mv2d_iter8.expect.sequence",
    1: "contract.ir#c_rep12_src_fixed.expect.sequence",
    2: "contract.ir#c_mv2d_iter6_partial_beat.expect.sequence",
    3: "contract.ir#c_mv2d_merge16.expect.sequence",
    4: "contract.ir#c_merge_fallback_mod4.expect.sequence",
    5: "contract.ir#c_ooo_completion_err.expect.final",
}

results = []   # (case_id, ok, point, expect, got, stim)
fails = []


def rec(cid, ok, point, exp, got, stim):
    results.append((cid, ok, point))
    if not ok:
        fails.append({"anchor": CASE_ANCHOR[cid] if cid in CASE_ANCHOR else "?",
                      "point": point, "expect": exp, "got": got, "stim": stim})


def parse_log(path):
    us, fs, ds = [], [], []
    for line in open(path):
        p = line.split()
        if not p:
            continue
        if p[0] == "U":
            us.append({"cyc": int(p[1]), "port": int(p[2]), "src": int(p[3]),
                       "dst": int(p[4]), "dim": int(p[5]), "uid": int(p[6])})
        elif p[0] == "F":
            fs.append({"cyc": int(p[1]), "inst_id": int(p[2]), "err": int(p[3])})
        elif p[0] == "D":
            ds.append({"cyc": int(p[1]), "port": int(p[2]), "uid": int(p[3]),
                       "err": int(p[4])})
    return us, fs, ds


def main():
    exp_cases = json.load(open(os.path.join(HERE, "l1_expected.json")))
    total = n_ok = 0
    for cid, case in enumerate(exp_cases):
        path = os.path.join(HERE, f"l1_case{cid}.log")
        us, fs, ds = parse_log(path)
        muid = case["main_uid"]
        stim = f"{case['variant']} fields={case['fields']} inst_id={case['inst_id']}"
        # ---- 主 uid 微码序列逐点 ----
        mine = [u for u in us if u["uid"] == muid]
        exp = case["expect"]
        for i in range(max(len(mine), len(exp))):
            total += 1
            e = exp[i] if i < len(exp) else None
            g = mine[i] if i < len(mine) else None
            if e is None:
                rec(cid, False, f"ucode[{i}] 多发", None,
                    {k: g[k] for k in ("port", "src", "dst", "dim", "uid")}, stim)
            elif g is None:
                rec(cid, False, f"ucode[{i}] 缺发",
                    {k: e[k] for k in ("port", "src_addr", "dst_addr", "dim_size", "uid")},
                    None, stim)
            else:
                gv = {"port": g["port"], "src_addr": g["src"], "dst_addr": g["dst"],
                      "dim_size": g["dim"], "uid": g["uid"]}
                ev = {k: e[k] for k in ("port", "src_addr", "dst_addr", "dim_size", "uid")}
                rec(cid, gv == ev, f"ucode[{i}]@port{e['port']}", ev, gv, stim)
        # ---- c_ooo 专项 ----
        if case["id"] == "c_ooo_completion_err":
            total += 2
            fin = case["final"]
            one = [x for x in fs if x["inst_id"] == case["inst_id"]]
            rec(cid, len(one) == 1 and one and one[0]["err"] == fin["err"],
                "inst_done 恰一条且 err 聚合", fin,
                one if one else fs, stim)
            last_done = max(d["cyc"] for d in ds) if ds else -1
            rec(cid, all(x["cyc"] >= last_done for x in one) and len(one) == 1,
                "收满前无 inst_done（F 不早于第 4 个 done；同拍合法=组合聚合）",
                "F.cyc > last_done_cyc", {"F": one, "last_done_cyc": last_done}, stim)

    for cid, case in enumerate(exp_cases):
        oks = [r for r in results if r[0] == cid]
        n_case = sum(1 for r in oks if r[1])
        print(f"== {case['id']}: {n_case}/{len(oks)} MATCH")
        for r in oks:
            mark = "OK " if r[1] else "XX "
            print(f"   {mark}{r[2]}")
    n_ok = sum(1 for r in results if r[1])
    print(f"\nL1 总点数 {n_ok}/{total} " + ("ALL MATCH" if n_ok == total else "HAS FAIL"))
    json.dump(fails, open(os.path.join(HERE, "l1_fails.json"), "w"), indent=1)
    return 0 if n_ok == total else 1


if __name__ == "__main__":
    sys.exit(main())
