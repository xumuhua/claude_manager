# web-relay 使用说明（RELAY-FETCH1）

> AWS 机 16.176.157.153 上的**带鉴权 HTTP 正向代理**，供全部 agent 访问国内直连受限的海外站点（openai.com / deepmind.google / anthropic.com 等）。
> 哥哥口径：**通用代理，各 agent 一样用；直连优先，连不上才走桥（fallback，不 all in）。**

## 服务概况

| 项 | 值 |
|---|---|
| 地址 | `http://127.0.0.1:9538（经 SSH 隧道转发到 AWS 机；隧道建法见下文）`（HTTP 正向代理，支持绝对 URI + HTTPS CONNECT 隧道） |
| 鉴权 | Proxy-Authorization Basic，username 任意，password = token |
| 凭证文件 | 各专家机 `~/.web_relay`（600），内容形如 `http://user:<token>@16.176.157.153:9538` |
| 限制 | 仅 http/https 出向；单连接空闲超时 60s；每 IP 并发 20 |
| 日志 | AWS 机 `journalctl -u web-relay`（只记 host/状态/耗时，不记凭证、不记完整 URL） |

token 由亦菲经 root 管道下发（AWS 机 `/root/web-relay-token.txt`）。**token 不落 git、不落群消息、不落任何文档明文。**

## 使用方式

### 1. 环境变量（推荐）

```bash
# ~/.web_relay 由亦菲下发，600 权限
export https_proxy=$(cat ~/.web_relay)
export http_proxy=$(cat ~/.web_relay)

curl -sI https://openai.com | head -1          # 应 200（或站点自身的 301/403 反爬，但不再是被墙的 000）
python3 -c "import requests; print(requests.get('https://www.anthropic.com', timeout=20).status_code)"
```

requests / httpx / aiohttp / scrapy 均自动认 `http_proxy`/`https_proxy` 环境变量，无需改代码。

### 2. 直连优先、走桥兜底（降级纪律）

```bash
fetch() {
  local url="$1"
  if curl -s -o /dev/null -m 8 "$url"; then
    curl -s -m 30 "$url"                                   # 直连优先
  else
    curl -s -m 30 -x "$(cat ~/.web_relay)" "$url"          # 连不上才走桥
  fi
}
```

Python 侧同理：先 `requests.get(url, timeout=8)` 直连，抛 ConnectionError/Timeout 才 `proxies={'http': relay, 'https': relay}` 重试。**不要默认全量走桥**——桥是兜底，不是主干。

### 3. 单次 curl 示例

```bash
curl -s -x "$(cat ~/.web_relay)" https://deepmind.google -o /tmp/dm.html
```

## Fallback：安全组 9538 未放行时走 SSH 隧道

若从本机 `nc 16.176.157.153 9538` 不通（安全组未放行 9538），在**需要走桥的机器上**挂隧道：

```bash
ssh -i ~/keys/manager.pem -o ExitOnForwardFailure=yes -N -L 9538:127.0.0.1:9538 ubuntu@16.176.157.153 &
# 然后代理地址换成 127.0.0.1
export https_proxy="http://user:<token>@127.0.0.1:9538"
```

隧道转发的是本机回环，token 不经公网代理入口，行为与直连 9538 完全一致。

## 运维

```bash
# 服务状态 / 重启（AWS 机 ubuntu 免密 sudo）
systemctl status web-relay
sudo systemctl restart web-relay
sudo journalctl -u web-relay -f

# 部署位置
/home/ubuntu/web-relay/web_relay.py    # aiohttp-free 纯 asyncio 实现（CONNECT 隧道 + 绝对 URI）
/home/ubuntu/web-relay/.env            # 600，WEB_RELAY_TOKEN / RELAY_HOST / RELAY_PORT
/etc/systemd/system/web-relay.service  # Restart=always
```

## 注意

- openai.com 对数据中心 IP 有 Cloudflare 反爬：经代理可能仍回 403 页面，但**连接可达**（对照国内直连 000 超时）。遇到反爬 403 属站点策略，不是桥故障；必要时换 UA 重试。
- 每 IP 并发上限 20，爬虫请自限速；超了会吃 429。
- 空闲 60s 自动断连，长轮询/大文件下载注意重试。
