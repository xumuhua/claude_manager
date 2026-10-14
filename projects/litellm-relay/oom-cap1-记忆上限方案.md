# OOM-CAP1 supervisor cgroup 记忆上限方案（2026-10-14 交付）

## 病理定案（三机实证）

- **203 机（115.191.75.203，六专家单元）**：`supervisor-*.service` 用户级单元（每专家账号
  `~/.config/systemd/user/`）全部 `MemoryMax=4294967296`（4G 硬限）+ 无 MemoryHigh。
  机器 16G、user slice 与机器级均无上限（`user-1008.slice MemoryMax=infinity`）。
  **今晨 09:41:26 三杀实证**（203 journal）：aicorp 单元内 claude.exe 单进程
  anon-rss 3.5G（total-vm 4.5G）顶爆 4G → memcg OOM 杀 claude.exe →
  systemd 级联 SIGTERM 收走单元全部三子（RetryExitStatus 一致性设计）。
  最坏预算=并发闸 3 × 3.5G = 10.5G < 12G 新上限 ⇒ 4G→12G 后该型三杀消除。
  当前现状：coder 4.13G（贴顶运行）、aichip 0.43G、其余空闲档。
- **181 机（115.190.14.181，d4/stockmodel/trace 三单元）**：单元文件【无 Memory 行】，
  runtime `MemoryMax=0/infinity`（无限额）→ cgroup OOM 三杀在该机**按构造不可能**，
  不动（加限反而引入新故障面；哥哥 9/23「观察项」口径维持）。
- **manager 机（64.190）**：无 supervisor 单元（专家不在此机），不适用。

派单原文「三机全部 supervisor-*.service unit 的 MemoryMax 上调」按实证修正为
「203 六单元上调；181 已是无限额无需上调；manager 不适用」——修的就是唯一灾区。

## 修法（203 六单元，user 级 drop-in，不动原 unit 文件）

每专家账号执行（原 unit 文件零改动，drop-in 覆盖）：

```bash
U=<专家名>; D=/home/$U/.config/systemd/user/supervisor-$U.service.d
mkdir -p $D
cat > $D/50-memory.conf <<'EOF'
[Service]
MemoryHigh=8589934592
MemoryMax=12884901888
EOF
chown -R $U:$U $D
sudo -u $U XDG_RUNTIME_DIR=/run/user/$(id -u $U) systemctl --user daemon-reload
```

- **MemoryHigh=8G（软限）**：超限后内核回收/限速该 cgroup，不杀进程——重活会话
  先被压速而非直接死刑；分配仍可超过 High 直到 Max。
- **MemoryMax=12G（硬限）**：机器 16G − 系统与基础服务 ~2.5G − 一定余量。
  12G 上限下即使触发 OOM 也只杀单进程，且概率大幅下降（现状最坏 10.5G 需求 <
  12G 供给）。oom_score_adj=200（既有）优先杀 claude.exe 本体，保 supervisor。
- **不重启任何单元**：MemoryMax/MemoryHigh 是 cgroup 属性，daemon-reload +
  `systemctl --user show -p MemoryMax` 即时生效（systemd 对运行中服务动态套用），
  在飞会话零中断——「滚动重启」步骤按实证取消，比派单预案更安全。

## 回滚

`rm /home/$U/.config/systemd/user/supervisor-$U.service.d/50-memory.conf && systemctl --user daemon-reload`（每账号一行，即时生效）。

## 顺手件（不入本单，登记观察）

- coder 单元当前 4.13G 贴 4G 顶（Max 未爆因 anon+file 计法）——本方案上线后自然
  获得喘息；若后续再观察到大内存常态，考虑 supervisor.py 侧给单会话加 RSS 上限
  提示（9/23 卡的 systemd-run scope 逃逸仍留档备查，暂不启用）。
