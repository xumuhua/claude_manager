# 新专家账号上岗手册（expert_onboarding）

> 当任务需要的能力在现有专家名册中没有对应专家时，在专家机（115.191.75.203，2026-08-28 起）新建专家账号并配置 claude 环境（8/28 前建于 manager 机 115.190.64.190，已全部迁走）。先例：`coder`（uid 1003）、`aichip`。

## 前提

- manager 持有专家机 root:`sshpass -p '<root密码>' ssh root@115.191.75.203`（密码与专家账号同一份，见 manager 记忆体 expert-team）。
- claude CLI 为系统级安装：`/usr/local/bin/claude`，新账号直接可用，无需重装。
- 认证 token 母本：manager 机 `~/keys/expert_claude_settings.json`（kimi k3 第三方 API 配置）。

## 步骤

### 1. 建号（root 下执行）

```bash
useradd -m -s /bin/bash <专家账号名>
echo '<专家账号名>:<统一密码>' | chpasswd
```

账号名即专家角色映射（如芯片专家 = `aichip`、程序员 = `coder`)，命名要见名知义。

### 2. 配置 claude 环境

认证走 `~/.claude/settings.json` 的 env（**非交互也可用**，这是红线——不能只配 .bashrc，否则无头任务跑不起来）:

```bash
mkdir -p /home/<专家账号名>/.claude
# 从 manager 机复制母本（或从已配好的专家机如 aichip 复制）
scp /home/manager/keys/expert_claude_settings.json \
    /home/<专家账号名>/.claude/settings.json
chown -R <专家账号名>:<专家账号名> /home/<专家账号名>/.claude
chmod 600 /home/<专家账号名>/.claude/settings.json
```

### 3. 验证上岗（必做，不验证不上岗）

```bash
ssh <专家账号名>@115.191.75.203 \
  'claude -p "回复两个字：正常" --dangerously-skip-permissions'
```

能正常返回内容才算通过。若报 `Not logged in`，检查 settings.json 的 env 是否生效（参考记忆体 expert-claude-cli-auth)。

### 4. 登记

- 写入任务工作空间 `专家名册.md`（账号、SSH、承担哪些 agent、验证时间）。
- 更新 manager 记忆体 `expert-team.md`，把新专家加入名册。

## 红线

- 密码沿用团队统一密码，不自创。
- settings.json 必须 600 权限、属主正确。
- 验证调用通过前，不得向该专家派发真实任务。
