"""gh_proxy 可选只读 PAT 自测（方案 B，2026-08-24 哥哥拍板）。纯单元测试，零网络。

用例：
 G1 配置缺省（无 github.token）→ gh_token=None
 G2 github.token="env:GITHUB_RO_TOKEN" 且 env 未设置 → None（匿名降级，不报错）
 G3 env 已设置 → 解析出 token 值
 G4 匿名路径回归：无 token 时 _session_headers 与旧版 _UA 逐键一致（无 Authorization）
 G5 PAT 路径：有 token 时注入 Authorization: Bearer <假 token>，其余头不变

用法：venv/bin/python tests/selftest_gh_token.py
退出码 0 = 全部通过。
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "server"))

import config as cfg_mod          # noqa: E402
import gh_proxy                   # noqa: E402

ok_all = True

def report(name, ok, detail=""):
    global ok_all
    ok_all = ok_all and ok
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))

BASE_YAML = """\
port: 8766
hub:
  url: http://127.0.0.1:8765
  token: "env:MP_TEST_HUB_TOKEN"
{github}
agents:
  - name: gege_dev
    role: gege
    token: "env:MP_TEST_GEGE_TOKEN"
    scope: [group, dm]
"""

def load_with(github_section):
    os.environ["MP_TEST_HUB_TOKEN"] = "t" * 32
    os.environ["MP_TEST_GEGE_TOKEN"] = "g" * 32
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
        f.write(BASE_YAML.format(github=github_section))
        path = f.name
    try:
        return cfg_mod.load_config(path)
    finally:
        os.unlink(path)

# G1：github 区整体缺省 → None
cfg = load_with("")
report("G1 无 github 配置 → gh_token=None", cfg["gh_token"] is None)

# G2：token 指向未设置的 env → None（降级不报错）
os.environ.pop("GITHUB_RO_TOKEN", None)
cfg = load_with('github:\n  token: "env:GITHUB_RO_TOKEN"\n')
report("G2 env:GITHUB_RO_TOKEN 未设置 → None（匿名降级，不阻塞加载）",
       cfg["gh_token"] is None)

# G3：env 已设置 → 解析出值
os.environ["GITHUB_RO_TOKEN"] = "fake-readonly-pat-123"
cfg = load_with('github:\n  token: "env:GITHUB_RO_TOKEN"\n')
report("G3 env 已设置 → 解析出 token", cfg["gh_token"] == "fake-readonly-pat-123",
       repr(cfg["gh_token"]))

# G4：匿名路径回归——无 token 时头部与旧版 _UA 逐键一致
h = gh_proxy._session_headers({"gh_token": None})
report("G4 匿名路径头部与旧版一致（无 Authorization）",
       h == gh_proxy._UA and "Authorization" not in h, str(h))

# G5：PAT 路径——注入 Authorization: Bearer <token>，UA/Accept 不变
h = gh_proxy._session_headers({"gh_token": "fake-readonly-pat-123"})
report("G5 PAT 路径注入 Authorization 头",
       h.get("Authorization") == "Bearer fake-readonly-pat-123"
       and h["User-Agent"] == gh_proxy._UA["User-Agent"]
       and h["Accept"] == gh_proxy._UA["Accept"], str(h))

os.environ.pop("GITHUB_RO_TOKEN", None)
print("\n===== gh_proxy PAT 自测：", "全部通过" if ok_all else "存在失败", "=====")
sys.exit(0 if ok_all else 1)
