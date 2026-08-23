#!/bin/bash
# mp-backend 开发态启动脚本（含 doubao key env 注入）
# key 只从 /home/manager/keys/doubao.sh 读入环境变量，不写进任何代码/配置。
# 配置优先读 config.local.yaml（本地开发值，已 gitignore），缺省回退 config.yaml（env 引用模板）。
cd "$(dirname "$0")"
source /home/manager/keys/doubao.sh
export DOUBAO_ARK_KEY="$ANTHROPIC_AUTH_TOKEN"
# 语音凭证（openspeech 独立体系）到位后取消注释：
# export DOUBAO_SPEECH_APPID=...
# export DOUBAO_SPEECH_TOKEN=...
CFG="config.yaml"
[ -f config.local.yaml ] && CFG="config.local.yaml"
exec venv/bin/python server/app.py --config "$CFG"
