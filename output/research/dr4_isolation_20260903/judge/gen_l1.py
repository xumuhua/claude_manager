#!/usr/bin/env python3
"""D-R4 L1 判卷 · 期望值机算 + 激励生成（判卷棒，只判不修）。

期望值 = hlc_eval 对 contract.ir 各 case stimulus 的机算结果（R-E10-3 承接，
不抄钉值）；同时把 stimulus 编码为 210bit 指令字写 case_stim.vh 供 TB 消费。

背景指令（占槽用，判卷侧自备）：MV2D 普通路径 iter=4，dim=16, ss=20, ds=24
（ss≠dim 避免误触 merge），发射后不回 done → 槽位保持在途占用。
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CHIP = os.path.normpath(os.path.join(HERE, "..", "chip_design_ir"))
SPLIT = os.path.join(CHIP, "examples_vnext/inst_ucode_splitter")
sys.path.insert(0, os.path.join(CHIP, "tools/hlc_check"))
import hlc_eval  # noqa: E402

BG = {"src_addr": 0x10000, "dst_addr": 0x20000, "dim_size": 16, "iter": 4,
      "src_stride": 20, "dst_stride": 24}  # 背景指令字段


def pack_mv2d(f):
    v = 0
    v |= (0 & 3) << 208
    v |= (f["src_addr"] & 0xFFFFFFFF) << 176
    v |= (f["dst_addr"] & 0xFFFFFFFFFFFFFFFF) << 112
    v |= (f["dim_size"] & 0xFFFFFFFF) << 80
    v |= (f["iter"] & 0xFFFF) << 64
    v |= (f["src_stride"] & 0xFFFFFFFF) << 32
    v |= (f["dst_stride"] & 0xFFFFFFFF)
    return v


def pack_rep12(f):
    v = 0
    v |= (1 & 3) << 208
    v |= (f["src_addr"] & 0xFFFFFFFFFFFFFFFF) << 144
    v |= (f["dst_addr"] & 0xFFFFFFFFFFFFFFFF) << 80
    v |= (f["dim_size"] & 0xFFFFFFFF) << 48
    v |= (f["iter"] & 0xFFFF) << 32
    v |= (f["dst_stride"] & 0xFFFFFFFF)
    return v


def emit_case(ev, ev_rep, case, bg_count, main_uid):
    """机算主指令期望微码序列 + 组装 TB 激励描述。

    返回 dict：variant/fields/inst_id/bg_count/main_uid/expect[]，expect 元素
    = {port, src_addr, dst_addr, dim_size, uid}（全 hlc_eval 机算）。
    """
    variant = case["stimulus"]["payload"]["instruction"]["variant"]
    fields = case["stimulus"]["payload"]["instruction"]["fields"]
    inst_id = case["stimulus"]["payload"]["inst_id"]
    exp = []
    seq = case["expect"].get("sequence")
    if seq:
        for i, e in enumerate(seq):
            if variant == "MV2D":
                r = ev.call("mv2d_emit", [fields["src_addr"], fields["dst_addr"],
                                          fields["dim_size"], fields["iter"],
                                          fields["src_stride"], fields["dst_stride"],
                                          main_uid, i])
            else:
                r = ev_rep.call("rep12_emit", [fields["src_addr"], fields["dst_addr"],
                                               fields["dim_size"], fields["iter"],
                                               fields["dst_stride"], main_uid, i])
            port = int(e["port"][e["port"].index("[") + 1:e["port"].index("]")])
            exp.append({"port": port,
                        "src_addr": r["src_addr"].v, "dst_addr": r["dst_addr"].v,
                        "dim_size": r["dim_size"].v, "uid": r["uid"].v})
    return {"id": case["id"], "variant": variant, "fields": fields,
            "inst_id": inst_id, "bg_count": bg_count, "main_uid": main_uid,
            "expect": exp}


def main():
    cases = json.load(open(os.path.join(SPLIT, "contract.ir"), encoding="utf-8"))["contract_cases"]
    ev = hlc_eval.load_module(SPLIT, "mv2d_uop.hlc")
    ev_rep = hlc_eval.load_module(SPLIT, "rep12_uop.hlc")
    ev_err = hlc_eval.load_module(SPLIT, "err_reduce.hlc")
    ev_c = hlc_eval.load_module(SPLIT, "common.hlc")

    out = []
    for c in cases:
        # preset.uid = 场景假设（机检）：uid=k ⇒ TB 需 k 条背景指令占槽 0..k-1
        # c_ooo 的 preset 为 table 形态（uid 在 table[0].uid）
        pre = c["stimulus"].get("preset", {})
        if "uid" in pre:
            k = pre["uid"]
        elif "table" in pre:
            k = pre["table"][0]["uid"]
        else:
            k = 0
        if c["id"] == "c_ooo_completion_err":
            # 该 case 无 payload.instruction（setup 散文：c_mv2d_iter8 类 iter=4，
            # preset table.total=4）——判卷侧按 setup 自备字段
            c = dict(c)
            c["stimulus"] = dict(c["stimulus"])
            c["stimulus"]["payload"] = {
                "instruction": {"variant": "MV2D", "fields": dict(
                    {kk: vv for kk, vv in cases[0]["stimulus"]["payload"]["instruction"]["fields"].items()},
                    iter=4)},
                "inst_id": 7}
        out.append(emit_case(ev, ev_rep, c, k, k))

    # c_ooo_completion_err：err 聚合机算 + final 期望 + 主指令微码机算（uid=2）
    ooo = next(o for o in out if o["id"] == "c_ooo_completion_err")
    ooo["err_reduce"] = ev_err.call("err_reduce", [0b1000]).v  # 完成到达 err 集 {port3}
    ooo["final"] = {"inst_id": ooo["inst_id"], "err": ooo["err_reduce"]}
    f = ooo["fields"]
    ooo["expect"] = []
    for j in range(4):
        r = ev.call("mv2d_emit", [f["src_addr"], f["dst_addr"], f["dim_size"], f["iter"],
                                  f["src_stride"], f["dst_stride"], 2, j])
        ooo["expect"].append({"port": j, "src_addr": r["src_addr"].v,
                              "dst_addr": r["dst_addr"].v,
                              "dim_size": r["dim_size"].v, "uid": r["uid"].v})

    json.dump(out, open(os.path.join(HERE, "l1_expected.json"), "w"), indent=1)

    # case_stim.vh：每 case 主指令 210bit + inst_id + 背景指令
    with open(os.path.join(HERE, "case_stim.vh"), "w") as fp:
        bgv = pack_mv2d(BG)
        fp.write(f"`define BG_INSTR 210'h{bgv:053x}\n")
        fp.write(f"`define BG_INST_ID 4'hf\n")
        for i, o in enumerate(out):
            v = pack_mv2d(o["fields"]) if o["variant"] == "MV2D" else pack_rep12(o["fields"])
            fp.write(f"`define CASE{i}_INSTR 210'h{v:053x}\n")
            fp.write(f"`define CASE{i}_INST_ID 4'h{o['inst_id']:x}\n")
    print(f"gen: {len(out)} cases -> l1_expected.json / case_stim.vh")
    # 附机算量抽查打印
    print("merge_hit(iter8) =", ev_c.call("merge_hit", [256, 512, 64, 8]).v,
          " merge_hit(merge16) =", ev_c.call("merge_hit", [64, 64, 64, 16]).v,
          " merge_hit(fallback) =", ev_c.call("merge_hit", [6, 6, 6, 3]).v)


if __name__ == "__main__":
    main()
