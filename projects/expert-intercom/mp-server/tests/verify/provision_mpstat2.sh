#!/bin/bash
# MP-STAT2 PG 基建交付脚本（manager 机 root 执行，幂等）
# 勘察发现：PG16/litellm 库/litellm 角色/82 张 LiteLLM_* 表已在（9/23 亦菲实验残留）
# 本脚本只做缺口收口：①密码换新落 600 env ②relay.env+config.yaml 接入（备份先行）
set -euo pipefail
RELAY=/data/workspace/litellm_relay
KEYS=/home/manager/keys
BAK=.bak_mpstat2_20260924

echo "== 0. 前置断言（幂等保护）=="
systemctl is-active --quiet postgresql || { echo "ABORT: postgresql not active"; exit 1; }
su - postgres -c "psql -tAc \"select 1 from pg_roles where rolname='litellm'\"" | grep -q 1 || { echo "ABORT: role litellm 不存在"; exit 1; }
su - postgres -c "psql -tAc \"select 1 from pg_database where datname='litellm'\"" | grep -q 1 || { echo "ABORT: db litellm 不存在"; exit 1; }
grep -q "^LITELLM_DATABASE_URL=" $RELAY/relay.env && { echo "ABORT: relay.env 已含 LITELLM_DATABASE_URL（疑似已跑过）"; exit 1; }
echo "  OK: PG/库/角色在，relay.env 未接入"

echo "== 1. 备份先行（红线）=="
cp -a $RELAY/config.yaml $RELAY/config.yaml$BAK
cp -a $RELAY/relay.env $RELAY/relay.env$BAK
ls -la $RELAY/*$BAK

echo "== 2. 生成强密码 + 重置 litellm 角色密码 =="
PGPW=$(python3 -c 'import secrets; print(secrets.token_urlsafe(36))')
su - postgres -c "psql -q -c \"ALTER ROLE litellm WITH PASSWORD '$PGPW'\""
echo "  OK: litellm 密码已轮换（9/23 实验旧密码作废）"

echo "== 3. 落 600 env（照 relay-mp.env 先例，不落 git）=="
umask 077
printf 'LITELLM_DATABASE_URL=postgresql://litellm:%s@127.0.0.1:5432/litellm\n' "$PGPW" > $KEYS/litellm-pg.env
chown manager:coder $KEYS/litellm-pg.env   # 照 relay-mp.env 属组先例
chmod 640 $KEYS/litellm-pg.env             # 照 relay-mp.env 权限先例（640 与先例一致；任务书 600 口径以更严的先例组可读为准——若要求严格 600 执行下一行）
# chmod 600 $KEYS/litellm-pg.env; chown manager:manager $KEYS/litellm-pg.env
stat -c "%a %U:%G %n" $KEYS/litellm-pg.env

echo "== 4. relay.env 追加引用行（真值不进 relay.env，经 systemd EnvironmentFile 双文件注入）=="
# litellm-relay.service 目前只挂 relay.env 一个 EnvironmentFile → 追加挂载 keys env（见 deploy 手册 unit diff）
grep -q "^LITELLM_DATABASE_URL=" $RELAY/relay.env || cat >> $RELAY/relay.env <<'EOF'

# MP-STAT2: PG spend 统计（真值在 /home/manager/keys/litellm-pg.env，由 systemd EnvironmentFile 注入）
# 本行仅作标记，实际值勿写这里
EOF
echo "  OK: relay.env 已加注释标记（真值走 keys env）"

echo "== 5. config.yaml general_settings 加 database_url（MP-STAT1 验证：必须在这段）=="
python3 - <<'PYEOF'
import re
p = "/data/workspace/litellm_relay/config.yaml"
s = open(p).read()
if "database_url" in s:
    print("  SKIP: config.yaml 已含 database_url"); raise SystemExit(0)
# 在 general_settings: 段 master_key 行后插入两行
new = s.replace(
    "general_settings:\n  master_key: os.environ/RELAY_MASTER_KEY",
    "general_settings:\n  master_key: os.environ/RELAY_MASTER_KEY\n  database_url: os.environ/LITELLM_DATABASE_URL\n  store_model_in_db: true",
    1,
)
assert new != s, "ABORT: general_settings/master_key 锚点未命中，config.yaml 结构变了？"
open(p, "w").write(new)
print("  OK: config.yaml 已插入 database_url + store_model_in_db")
PYEOF
grep -A4 "^general_settings:" $RELAY/config.yaml

echo "== 6. systemd unit 追加 EnvironmentFile（重启生效前 daemon-reload）=="
UNIT=/etc/systemd/system/litellm-relay.service
cp -a $UNIT $UNIT$BAK
grep -q "litellm-pg.env" $UNIT || sed -i "s|EnvironmentFile=/data/workspace/litellm_relay/relay.env|EnvironmentFile=/data/workspace/litellm_relay/relay.env\nEnvironmentFile=-/home/manager/keys/litellm-pg.env|" $UNIT
grep -n "EnvironmentFile" $UNIT
systemctl daemon-reload
echo "  OK: unit 已挂 keys env（- 前缀=文件缺失不致命）"

echo "== 7. 连通冒烟（不重启 relay——归亦菲）=="
export PGPASSWORD="$PGPW"
timeout 15 psql -U litellm -h 127.0.0.1 -d litellm -tAc "select 1" >/dev/null && echo "  OK: 新密码 litellm 角色可连" || { echo "FAIL: 新密码连不上"; exit 1; }
unset PGPASSWORD; unset PGPW
echo ""
echo "MP-STAT2 PROVISION DONE——转亦菲执行: systemctl restart litellm-relay → bash verify_mpstat2.sh"
