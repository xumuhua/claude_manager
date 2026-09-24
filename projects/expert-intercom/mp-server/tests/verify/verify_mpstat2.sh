#!/bin/bash
# MP-STAT2 全链验证脚本（manager 机 root 执行，在 provision_mpstat2.sh + 亦菲重启 relay 之后跑）
# 七步：PG 基建 → spend 落行 → /spend/logs API → mp-backend stats_available 翻 true → rotator 共存
# 用法: bash verify_mpstat2.sh   （不重启任何服务）
set -u
PASS=0; FAIL=0
ok()  { echo "  [PASS] $1"; PASS=$((PASS+1)); }
bad() { echo "  [FAIL] $1"; FAIL=$((FAIL+1)); }

RELAY=/data/workspace/litellm_relay
MPB=/data/workspace/expert-intercom/mp-backend

echo "== step0 环境自检 =="
MK=$(cat $RELAY/master.key 2>/dev/null | tr -d '[:space:]')
[ -n "$MK" ] && ok "master.key 可读" || { bad "master.key 不可读"; echo "ABORT"; exit 1; }
# agent token：mp-backend config.local.yaml agents 段第一条（不落输出）
TOKEN=$(python3 -c "
import yaml
cfg = yaml.safe_load(open('$MPB/config.local.yaml'))
agents = cfg.get('agents') or []
a0 = agents[0] if isinstance(agents, list) else list(agents.items())[0][1]
print(a0['token'] if isinstance(a0, dict) else a0)
" 2>/dev/null)
[ -n "$TOKEN" ] && ok "agent token 取得（config.local.yaml agents[0]）" || { bad "agent token 取不到"; echo "ABORT"; exit 1; }

DBURL=$(grep -E "^LITELLM_DATABASE_URL=" /home/manager/keys/litellm-pg.env 2>/dev/null | cut -d= -f2-)
[ -n "$DBURL" ] && ok "litellm-pg.env 存在" || { bad "/home/manager/keys/litellm-pg.env 缺失——先跑 provision_mpstat2.sh"; echo "ABORT"; exit 1; }
export PGPASSWORD=$(echo "$DBURL" | sed -E "s|.*://[^:]*:([^@]*)@.*|\1|")
PSQL="timeout 15 psql -U litellm -h 127.0.0.1 -d litellm -t -A"

echo "== step1 PG 基建七查 =="
systemctl is-active --quiet postgresql && ok "postgresql active" || bad "postgresql not active"
ss -tln | grep -q "127.0.0.1:5432" && ok "5432 listen 127.0.0.1" || bad "5432 未监听 127.0.0.1"
ss -tln | grep -qE "(0\.0\.0\.0|\*|\[::\]):5432" && bad "5432 暴露外网（红线）" || ok "5432 未暴露外网"
$PSQL -c "select 1" >/dev/null 2>&1 && ok "litellm 角色可连（新密码）" || bad "litellm 角色连接失败"
TC=$($PSQL -c "select count(*) from information_schema.tables where table_schema='public' and table_name like 'LiteLLM_%'" 2>/dev/null)
[ "${TC:-0}" -ge 30 ] && ok "LiteLLM_* 表 ${TC} 张" || bad "LiteLLM_* 表仅 ${TC:-0} 张"
grep -A4 "^general_settings:" $RELAY/config.yaml | grep -q "database_url" \
  && ok "config.yaml general_settings 含 database_url" || bad "config.yaml general_settings 缺 database_url"
grep -q "litellm-pg.env" /etc/systemd/system/litellm-relay.service && ok "unit 已挂 litellm-pg.env" || bad "unit 未挂 keys env"
systemctl is-active --quiet litellm-relay && ok "litellm-relay active" || bad "litellm-relay not active（需亦菲先重启）"
# 当前 boot 无 prisma 报错（含跨 boot 历史误判）
journalctl -u litellm-relay -b --no-pager 2>/dev/null | grep -qiE "prisma.*(error|failed)|P1001|Can't reach database" \
  && bad "当前 boot relay 日志有 prisma 报错" || ok "当前 boot relay 日志无 prisma 报错"

echo "== step2 测试请求落 spend =="
BEFORE=$($PSQL -c 'select count(*) from "LiteLLM_SpendLogs"' 2>/dev/null || echo 0)
echo "  spend before=$BEFORE"
RESP=$(curl -s -m 60 -X POST http://127.0.0.1:9536/v1/chat/completions \
  -H "Authorization: Bearer $MK" -H "Content-Type: application/json" \
  -d '{"model":"kimi-han","messages":[{"role":"user","content":"MP-STAT2 verify: reply with the single word OK"}],"max_tokens":10}')
echo "$RESP" | grep -q '"content"' && ok "测试请求 200（9536/kimi-han 真实模型）" || echo "  [WARN] 测试请求非 200: $(echo "$RESP" | head -c 150)（仍查 spend）"
sleep 6   # spend 落库异步
AFTER=$($PSQL -c 'select count(*) from "LiteLLM_SpendLogs"' 2>/dev/null || echo 0)
echo "  spend after=$AFTER"
[ "${AFTER:-0}" -gt "${BEFORE:-0}" ] && ok "spend 落行 +$((AFTER-BEFORE))" || bad "spend 未新增（异步落库失败或请求未达）"

echo "== step3 /spend/logs API 出数 =="
SPEND=$(curl -s -m 20 "http://127.0.0.1:9536/spend/logs" -H "Authorization: Bearer $MK")
echo "$SPEND" | grep -qE "prompt_tokens|completion_tokens" && ok "/spend/logs 有 token 字段" || bad "/spend/logs 无 token 字段: $(echo "$SPEND" | head -c 150)"
echo "$SPEND" | grep -qE "totalTime|total_time|startTime" && ok "/spend/logs 有耗时字段" || bad "/spend/logs 无耗时字段"

echo "== step4 mp-backend stats_available 翻 true（最终验收点）=="
ST=$(curl -s -m 20 "http://127.0.0.1:8766/api/status/models" -H "Authorization: Bearer $TOKEN")
echo "$ST" | grep -q '"stats_available": *true' && ok "stats_available=true" || bad "stats_available 未翻 true（响应头 200 字节: $(echo "$ST" | head -c 200)）"
echo "$ST" | python3 -c '
import sys, json
try:
    d = json.load(sys.stdin)
except Exception as e:
    print(f"  响应非 JSON: {e}"); sys.exit(1)
stats = d.get("stats") or {}
hit = [m for m, s in stats.items() if (s or {}).get("avg_latency_ms") is not None]
print(f"  stats 非空模型数={len(hit)}/{len(stats)}: {hit[:5]}")
sys.exit(0 if hit else 1)
' && ok "stats 有真数据（avg_latency_ms 非 null）" || bad "stats 全 null"

echo "== step5 rotator 与 PG 共存 =="
cd $RELAY && timeout 90 sudo -u manager $RELAY/venv/bin/python rotator.py --once >/tmp/mpstat2_rotator.out 2>&1
RC=$?
[ $RC -eq 0 ] && ok "rotator --once 跑通（rc=0）" || bad "rotator --once rc=$RC: $(tail -3 /tmp/mpstat2_rotator.out)"
systemctl is-active --quiet litellm-relay && ok "rotator 后 relay 仍 active" || bad "rotator 后 relay 挂了"

echo ""
echo "== 结论: PASS=$PASS FAIL=$FAIL =="
[ $FAIL -eq 0 ] && echo "MP-STAT2 VERIFY: ALL GREEN" || echo "MP-STAT2 VERIFY: HAS FAILURES"
exit $FAIL
