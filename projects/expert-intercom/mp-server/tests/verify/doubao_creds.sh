#!/bin/bash
# doubao 凭证最小验证脚本（A-mp / F6 前置验证）
# 用法: source /home/manager/keys/doubao.sh && bash doubao_creds.sh
# 纪律: key 只从 env 读，本脚本不打印完整 key
set -u
KEY="${ANTHROPIC_AUTH_TOKEN:?缺 ANTHROPIC_AUTH_TOKEN}"
BASE="${ANTHROPIC_BASE_URL:-https://ark.cn-beijing.volces.com/api/plan}"
MODEL="${ANTHROPIC_MODEL:-ark-code-latest}"
V3="https://ark.cn-beijing.volces.com/api/v3"
echo "key 指纹: ${KEY:0:8}…${KEY: -4}  base=$BASE  model=$MODEL"

req () { # $1=label $2=url $3=body $4=extra-headers
  local label="$1" url="$2" body="$3" hdr="${4:-}"
  echo "===== [$label] POST $url"
  if [ -n "$hdr" ]; then
    curl -sS -m 30 -o /tmp/db_resp.json -w "HTTP %{http_code}  %{time_total}s\n" \
      -X POST "$url" -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" -H "$hdr" -d "$body"
  else
    curl -sS -m 30 -o /tmp/db_resp.json -w "HTTP %{http_code}  %{time_total}s\n" \
      -X POST "$url" -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" -d "$body"
  fi
  head -c 600 /tmp/db_resp.json; echo
}

# 1) 文本摘要：Anthropic 兼容端点 /api/plan/v1/messages（x-api-key 风格也试）
echo "===== [1a] Anthropic 兼容 /api/plan/v1/messages (x-api-key)"
curl -sS -m 30 -o /tmp/db_resp.json -w "HTTP %{http_code}  %{time_total}s\n" \
  -X POST "$BASE/v1/messages" -H "x-api-key: $KEY" -H "anthropic-version: 2023-06-01" -H "Content-Type: application/json" \
  -d "{\"model\":\"$MODEL\",\"max_tokens\":128,\"messages\":[{\"role\":\"user\",\"content\":\"用一句话总结：专家互通工具是让多个 AI 专家在消息总线上协作的系统。\"}]}"
head -c 600 /tmp/db_resp.json; echo

req "1b Anthropic 兼容 Bearer" "$BASE/v1/messages" "{\"model\":\"$MODEL\",\"max_tokens\":128,\"messages\":[{\"role\":\"user\",\"content\":\"回复 ok 两个字即可\"}]}"

# 2) OpenAI 兼容 /api/v3/chat/completions
req "2 OpenAI 兼容 /api/v3 chat" "$V3/chat/completions" "{\"model\":\"$MODEL\",\"max_tokens\":32,\"messages\":[{\"role\":\"user\",\"content\":\"回复 ok 两个字即可\"}]}"

# 3) ASR：openspeech 录音文件识别（大模型）—— 探测鉴权体系
echo "===== [3a] openspeech ASR (Ark key 直调探测)"
curl -sS -m 30 -o /tmp/db_resp.json -w "HTTP %{http_code}  %{time_total}s\n" \
  -X POST "https://openspeech.bytedance.com/api/v3/auc/bigmodel/recognize/flash" \
  -H "X-Api-App-Key: $KEY" -H "X-Api-Access-Key: $KEY" -H "X-Api-Resource-Id: volc.bigasr.auc_turbo" -H "X-Api-Request-Id: probe-$(date +%s)" -H "Content-Type: application/json" \
  -d '{"user":{"uid":"probe"},"audio":{"url":"https://example.com/x.mp3"},"request":{"model_name":"bigmodel","enable_itn":true}}'
head -c 400 /tmp/db_resp.json; echo
echo "--- 响应头（状态行在 X-Api-Status-Code）:"
curl -sS -m 30 -D - -o /dev/null -X POST "https://openspeech.bytedance.com/api/v3/auc/bigmodel/recognize/flash" \
  -H "X-Api-App-Key: $KEY" -H "X-Api-Access-Key: $KEY" -H "X-Api-Resource-Id: volc.bigasr.auc_turbo" -H "X-Api-Request-Id: probe2-$(date +%s)" -H "Content-Type: application/json" \
  -d '{"user":{"uid":"probe"},"audio":{"url":"https://example.com/x.mp3"}}' | grep -i "x-api\|HTTP/"

# 4) TTS：openspeech HTTP 合成
echo "===== [4a] openspeech TTS (Ark key 直调探测)"
curl -sS -m 30 -o /tmp/db_resp.json -w "HTTP %{http_code}  %{time_total}s\n" \
  -X POST "https://openspeech.bytedance.com/api/v1/tts" \
  -H "Authorization: Bearer;$KEY" -H "Content-Type: application/json" \
  -d '{"app":{"appid":"unknown","token":"unknown","cluster":"volcano_tts"},"user":{"uid":"probe"},"audio":{"voice_type":"zh_male_M392_conversation_wvae_bigtts","encoding":"mp3"},"request":{"reqid":"probe","text":"测试","operation":"query"}}'
head -c 400 /tmp/db_resp.json; echo

# 5) Ark /api/v3 音频端点探测（OpenAI 兼容形态是否存在）
req "5a Ark /api/v3/audio/speech" "$V3/audio/speech" '{"model":"doubao-tts","input":"测试","voice":"zh_male_M392_conversation_wvae_bigtts","response_format":"mp3"}'
req "5b Ark /api/v3/audio/transcriptions" "$V3/audio/transcriptions" '{"model":"doubao-asr","prompt":"probe"}'
