#!/usr/bin/env bun
/**
 * WeChat channel for Claude Code.
 *
 * Bridges WeChat messages into a Claude Code session via Tencent's iLink Bot API.
 * No OpenClaw dependency — talks directly to ilinkai.weixin.qq.com.
 *
 * Flow: QR code login → long-poll getUpdates → MCP notification → Claude → reply tool → sendMessage
 */

import { Server } from '@modelcontextprotocol/sdk/server/index.js'
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js'
import {
  ListToolsRequestSchema,
  CallToolRequestSchema,
} from '@modelcontextprotocol/sdk/types.js'
import { randomBytes, randomUUID } from 'crypto'
import { readFileSync, writeFileSync, mkdirSync } from 'fs'
import { homedir } from 'os'
import { join } from 'path'

// ── Config ──────────────────────────────────────────────────────────────────

const STATE_DIR = process.env.WECHAT_STATE_DIR ?? join(homedir(), '.claude', 'channels', 'wechat')
const ACCOUNT_FILE = join(STATE_DIR, 'account.json')
const SYNC_BUF_FILE = join(STATE_DIR, 'sync-buf.txt')

const DEFAULT_BASE_URL = 'https://ilinkai.weixin.qq.com'
const DEFAULT_BOT_TYPE = '3'
const LONG_POLL_TIMEOUT_MS = 35_000
const MAX_CONSECUTIVE_FAILURES = 3
const BACKOFF_DELAY_MS = 30_000
const RETRY_DELAY_MS = 2_000
const TEXT_CHUNK_LIMIT = 4000
// WECHAT-FIX1（2026-09-13 哥哥实测 >4000 字切段连发，腾讯 BOT 通道限流吞后续段）：
// 段间延时 + 单段失败重试 1 次；重试间隔走既有 RETRY_DELAY_MS。
const CHUNK_SEND_DELAY_MS = 900
const CHUNK_SEND_MAX_ATTEMPTS = 2

// ── iLink API Types ─────────────────────────────────────────────────────────

type BaseInfo = { channel_version?: string }

type MessageItemType = 0 | 1 | 2 | 3 | 4 | 5
const MsgItemType = { NONE: 0, TEXT: 1, IMAGE: 2, VOICE: 3, FILE: 4, VIDEO: 5 } as const

type TextItem = { text?: string }
type ImageItem = { media?: { encrypt_query_param?: string; aes_key?: string }; url?: string }
type VoiceItem = { media?: { encrypt_query_param?: string }; text?: string; playtime?: number }
type FileItem = { media?: { encrypt_query_param?: string }; file_name?: string }
type VideoItem = { media?: { encrypt_query_param?: string } }

type MessageItem = {
  type?: number
  text_item?: TextItem
  image_item?: ImageItem
  voice_item?: VoiceItem
  file_item?: FileItem
  video_item?: VideoItem
  ref_msg?: { title?: string; message_item?: MessageItem }
  msg_id?: string
}

type WeixinMessage = {
  seq?: number
  message_id?: number
  from_user_id?: string
  to_user_id?: string
  client_id?: string
  create_time_ms?: number
  session_id?: string
  message_type?: number
  message_state?: number
  item_list?: MessageItem[]
  context_token?: string
}

type GetUpdatesResp = {
  ret?: number
  errcode?: number
  errmsg?: string
  msgs?: WeixinMessage[]
  get_updates_buf?: string
  longpolling_timeout_ms?: number
}

// ── iLink API Client ────────────────────────────────────────────────────────

function buildBaseInfo(): BaseInfo {
  return { channel_version: '0.0.1' }
}

function randomWechatUin(): string {
  const uint32 = randomBytes(4).readUInt32BE(0)
  return Buffer.from(String(uint32), 'utf-8').toString('base64')
}

function buildHeaders(token?: string, body?: string): Record<string, string> {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    AuthorizationType: 'ilink_bot_token',
    'X-WECHAT-UIN': randomWechatUin(),
  }
  if (body) headers['Content-Length'] = String(Buffer.byteLength(body, 'utf-8'))
  if (token?.trim()) headers.Authorization = `Bearer ${token.trim()}`
  return headers
}

async function apiFetch(params: {
  baseUrl: string; endpoint: string; body: string;
  token?: string; timeoutMs: number; label: string;
}): Promise<string> {
  const base = params.baseUrl.endsWith('/') ? params.baseUrl : `${params.baseUrl}/`
  const url = new URL(params.endpoint, base).toString()
  const hdrs = buildHeaders(params.token, params.body)
  const controller = new AbortController()
  const t = setTimeout(() => controller.abort(), params.timeoutMs)
  try {
    const res = await fetch(url, {
      method: 'POST', headers: hdrs, body: params.body, signal: controller.signal,
    })
    clearTimeout(t)
    const rawText = await res.text()
    if (!res.ok) throw new Error(`${params.label} ${res.status}: ${rawText}`)
    return rawText
  } catch (err) {
    clearTimeout(t)
    throw err
  }
}

async function ilinkGetUpdates(params: {
  baseUrl: string; token?: string; getUpdatesBuf: string; timeoutMs?: number;
}): Promise<GetUpdatesResp> {
  const timeout = params.timeoutMs ?? LONG_POLL_TIMEOUT_MS
  try {
    const raw = await apiFetch({
      baseUrl: params.baseUrl,
      endpoint: 'ilink/bot/getupdates',
      body: JSON.stringify({ get_updates_buf: params.getUpdatesBuf, base_info: buildBaseInfo() }),
      token: params.token,
      timeoutMs: timeout,
      label: 'getUpdates',
    })
    return JSON.parse(raw) as GetUpdatesResp
  } catch (err) {
    if (err instanceof Error && err.name === 'AbortError') {
      return { ret: 0, msgs: [], get_updates_buf: params.getUpdatesBuf }
    }
    throw err
  }
}

async function ilinkSendMessage(params: {
  baseUrl: string; token?: string; to: string; text: string; contextToken?: string;
}): Promise<string> {
  const clientId = `cc-wechat-${randomUUID().slice(0, 8)}`
  await apiFetch({
    baseUrl: params.baseUrl,
    endpoint: 'ilink/bot/sendmessage',
    body: JSON.stringify({
      msg: {
        from_user_id: '',
        to_user_id: params.to,
        client_id: clientId,
        message_type: 2, // BOT
        message_state: 2, // FINISH
        item_list: params.text ? [{ type: MsgItemType.TEXT, text_item: { text: params.text } }] : undefined,
        context_token: params.contextToken,
      },
      base_info: buildBaseInfo(),
    }),
    token: params.token,
    timeoutMs: 15_000,
    label: 'sendMessage',
  })
  return clientId
}

async function ilinkSendTyping(params: {
  baseUrl: string; token?: string; userId: string; typingTicket: string; status: number;
}): Promise<void> {
  await apiFetch({
    baseUrl: params.baseUrl,
    endpoint: 'ilink/bot/sendtyping',
    body: JSON.stringify({
      ilink_user_id: params.userId,
      typing_ticket: params.typingTicket,
      status: params.status,
      base_info: buildBaseInfo(),
    }),
    token: params.token,
    timeoutMs: 10_000,
    label: 'sendTyping',
  }).catch(() => {}) // best-effort
}

async function ilinkGetConfig(params: {
  baseUrl: string; token?: string; userId: string; contextToken?: string;
}): Promise<{ typing_ticket?: string }> {
  try {
    const raw = await apiFetch({
      baseUrl: params.baseUrl,
      endpoint: 'ilink/bot/getconfig',
      body: JSON.stringify({
        ilink_user_id: params.userId,
        context_token: params.contextToken,
        base_info: buildBaseInfo(),
      }),
      token: params.token,
      timeoutMs: 10_000,
      label: 'getConfig',
    })
    return JSON.parse(raw)
  } catch {
    return {}
  }
}

// ── QR Code Login ───────────────────────────────────────────────────────────

type QRCodeResponse = { qrcode: string; qrcode_img_content: string }
type StatusResponse = {
  status: 'wait' | 'scaned' | 'confirmed' | 'expired'
  bot_token?: string
  ilink_bot_id?: string
  baseurl?: string
  ilink_user_id?: string
}

async function fetchQRCode(apiBaseUrl: string): Promise<QRCodeResponse> {
  const base = apiBaseUrl.endsWith('/') ? apiBaseUrl : `${apiBaseUrl}/`
  const url = `${base}ilink/bot/get_bot_qrcode?bot_type=${DEFAULT_BOT_TYPE}`
  const res = await fetch(url)
  if (!res.ok) throw new Error(`Failed to fetch QR code: ${res.status}`)
  return await res.json() as QRCodeResponse
}

async function pollQRStatus(apiBaseUrl: string, qrcode: string): Promise<StatusResponse> {
  const base = apiBaseUrl.endsWith('/') ? apiBaseUrl : `${apiBaseUrl}/`
  const url = `${base}ilink/bot/get_qrcode_status?qrcode=${encodeURIComponent(qrcode)}`
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), LONG_POLL_TIMEOUT_MS)
  try {
    const res = await fetch(url, {
      headers: { 'iLink-App-ClientVersion': '1' },
      signal: controller.signal,
    })
    clearTimeout(timer)
    if (!res.ok) throw new Error(`QR status poll failed: ${res.status}`)
    return await res.json() as StatusResponse
  } catch (err) {
    clearTimeout(timer)
    if (err instanceof Error && err.name === 'AbortError') return { status: 'wait' }
    throw err
  }
}

type AccountData = {
  token: string
  baseUrl: string
  botId: string
  userId?: string
  savedAt: string
}

function loadAccount(): AccountData | null {
  try {
    return JSON.parse(readFileSync(ACCOUNT_FILE, 'utf8')) as AccountData
  } catch { return null }
}

function saveAccount(data: AccountData): void {
  mkdirSync(STATE_DIR, { recursive: true, mode: 0o700 })
  const tmp = ACCOUNT_FILE + '.tmp'
  writeFileSync(tmp, JSON.stringify(data, null, 2), { mode: 0o600 })
  const { renameSync } = require('fs')
  renameSync(tmp, ACCOUNT_FILE)
}

async function doQRLogin(baseUrl: string): Promise<AccountData> {
  process.stderr.write('wechat channel: starting QR code login...\n')

  const qr = await fetchQRCode(baseUrl)
  process.stderr.write('\n使用微信扫描以下二维码以完成连接：\n\n')

  try {
    const qrterm = await import('qrcode-terminal')
    await new Promise<void>(resolve => {
      qrterm.default.generate(qr.qrcode_img_content, { small: true }, (output: string) => {
        process.stderr.write(output + '\n')
        resolve()
      })
    })
  } catch {
    process.stderr.write(`二维码链接: ${qr.qrcode_img_content}\n`)
  }

  process.stderr.write('\n等待扫码...\n')

  const deadline = Date.now() + 480_000
  let scannedPrinted = false

  while (Date.now() < deadline) {
    const status = await pollQRStatus(baseUrl, qr.qrcode)

    switch (status.status) {
      case 'wait':
        break
      case 'scaned':
        if (!scannedPrinted) {
          process.stderr.write('👀 已扫码，在微信中确认...\n')
          scannedPrinted = true
        }
        break
      case 'expired':
        throw new Error('二维码已过期，请重启重试')
      case 'confirmed': {
        if (!status.ilink_bot_id || !status.bot_token) {
          throw new Error('登录失败：服务器未返回 bot token')
        }
        const account: AccountData = {
          token: status.bot_token,
          baseUrl: status.baseurl ?? baseUrl,
          botId: status.ilink_bot_id,
          userId: status.ilink_user_id,
          savedAt: new Date().toISOString(),
        }
        saveAccount(account)
        process.stderr.write(`✅ 微信连接成功！botId=${account.botId}\n`)
        return account
      }
    }

    await new Promise(r => setTimeout(r, 1000))
  }

  throw new Error('登录超时')
}

// ── Access: scan QR = authenticated, all messages forwarded ─────────────────

// ── Context Token Store ─────────────────────────────────────────────────────

// context_token is per-message from getUpdates, must be echoed in every reply
const contextTokenStore = new Map<string, string>()

// ── Typing Ticket Cache ─────────────────────────────────────────────────────

const typingTicketCache = new Map<string, { ticket: string; fetchedAt: number }>()
const TYPING_TICKET_TTL_MS = 5 * 60_000

async function getTypingTicket(account: AccountData, userId: string, contextToken?: string): Promise<string | undefined> {
  const cached = typingTicketCache.get(userId)
  if (cached && Date.now() - cached.fetchedAt < TYPING_TICKET_TTL_MS) return cached.ticket
  const cfg = await ilinkGetConfig({
    baseUrl: account.baseUrl,
    token: account.token,
    userId,
    contextToken,
  })
  if (cfg.typing_ticket) {
    typingTicketCache.set(userId, { ticket: cfg.typing_ticket, fetchedAt: Date.now() })
  }
  return cfg.typing_ticket
}

// ── Sync Buf Persistence ────────────────────────────────────────────────────

function loadSyncBuf(): string {
  try { return readFileSync(SYNC_BUF_FILE, 'utf8').trim() } catch { return '' }
}

function saveSyncBuf(buf: string): void {
  mkdirSync(STATE_DIR, { recursive: true })
  writeFileSync(SYNC_BUF_FILE, buf)
}

// ── Text Chunking ───────────────────────────────────────────────────────────

function chunk(text: string, limit: number): string[] {
  if (text.length <= limit) return [text]
  const out: string[] = []
  let rest = text
  while (rest.length > limit) {
    const para = rest.lastIndexOf('\n\n', limit)
    const line = rest.lastIndexOf('\n', limit)
    const space = rest.lastIndexOf(' ', limit)
    const cut = para > limit / 2 ? para : line > limit / 2 ? line : space > 0 ? space : limit
    out.push(rest.slice(0, cut))
    rest = rest.slice(cut).replace(/^\n+/, '')
  }
  if (rest) out.push(rest)
  return out
}

// ── Extract text body from message ──────────────────────────────────────────

function extractText(msg: WeixinMessage): string {
  if (!msg.item_list?.length) return ''
  for (const item of msg.item_list) {
    if (item.type === MsgItemType.TEXT && item.text_item?.text != null) {
      const text = String(item.text_item.text)
      const ref = item.ref_msg
      if (!ref) return text
      const parts: string[] = []
      if (ref.title) parts.push(ref.title)
      if (!parts.length) return text
      return `[引用: ${parts.join(' | ')}]\n${text}`
    }
    if (item.type === MsgItemType.VOICE && item.voice_item?.text) {
      return item.voice_item.text
    }
  }
  return ''
}

function getMediaInfo(msg: WeixinMessage): { kind: string; info: string } | undefined {
  if (!msg.item_list?.length) return undefined
  for (const item of msg.item_list) {
    if (item.type === MsgItemType.IMAGE) return { kind: 'image', info: '(图片)' }
    if (item.type === MsgItemType.VIDEO) return { kind: 'video', info: '(视频)' }
    if (item.type === MsgItemType.FILE) return { kind: 'file', info: `(文件: ${item.file_item?.file_name ?? 'unknown'})` }
    if (item.type === MsgItemType.VOICE && !item.voice_item?.text) {
      return { kind: 'voice', info: `(语音 ${item.voice_item?.playtime ? `${Math.ceil(item.voice_item.playtime / 1000)}s` : ''})` }
    }
  }
  return undefined
}

// ── Strip Markdown for WeChat ───────────────────────────────────────────────

function stripMarkdown(text: string): string {
  let result = text
  result = result.replace(/```[^\n]*\n?([\s\S]*?)```/g, (_, code: string) => code.trim())
  result = result.replace(/!\[[^\]]*\]\([^)]*\)/g, '')
  result = result.replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
  result = result.replace(/^\|[\s:|-]+\|$/gm, '')
  result = result.replace(/^\|(.+)\|$/gm, (_, inner: string) =>
    inner.split('|').map(cell => cell.trim()).join('  '),
  )
  // Bold/italic/strikethrough
  result = result.replace(/\*\*\*(.+?)\*\*\*/g, '$1')
  result = result.replace(/\*\*(.+?)\*\*/g, '$1')
  result = result.replace(/\*(.+?)\*/g, '$1')
  result = result.replace(/__(.+?)__/g, '$1')
  result = result.replace(/_(.+?)_/g, '$1')
  result = result.replace(/~~(.+?)~~/g, '$1')
  // Inline code
  result = result.replace(/`([^`]+)`/g, '$1')
  // Headers
  result = result.replace(/^#{1,6}\s+/gm, '')
  return result
}

// ── MCP Server ──────────────────────────────────────────────────────────────

const mcp = new Server(
  { name: 'wechat', version: '0.0.1' },
  {
    capabilities: { tools: {}, experimental: { 'claude/channel': {} } },
    instructions: [
      '对方使用微信，你的终端输出他看不到。所有回复必须通过 reply 工具发送。',
      '',
      '微信消息到达格式: <channel source="wechat" chat_id="..." message_id="..." user="..." ts="...">。',
      '使用 reply 工具回复，传入 chat_id。',
      '',
      '微信 Bot API 没有历史记录或搜索功能，你只能看到实时到达的消息。如需更早的上下文，让用户粘贴或复述。',
    ].join('\n'),
  },
)

mcp.setRequestHandler(ListToolsRequestSchema, async () => ({
  tools: [
    {
      name: 'reply',
      description: '在微信中回复消息。传入 inbound 消息的 chat_id。',
      inputSchema: {
        type: 'object',
        properties: {
          chat_id: { type: 'string', description: '微信用户 ID（来自 <channel> 标签的 chat_id）' },
          text: { type: 'string', description: '要发送的消息文本' },
        },
        required: ['chat_id', 'text'],
      },
    },
    {
      name: 'edit_message',
      description: '编辑之前发送的消息（发送新消息替代，微信不支持真编辑）。',
      inputSchema: {
        type: 'object',
        properties: {
          chat_id: { type: 'string' },
          text: { type: 'string' },
        },
        required: ['chat_id', 'text'],
      },
    },
  ],
}))

mcp.setRequestHandler(CallToolRequestSchema, async req => {
  const args = (req.params.arguments ?? {}) as Record<string, unknown>
  try {
    switch (req.params.name) {
      case 'reply':
      case 'edit_message': {
        const chatId = args.chat_id as string
        const text = args.text as string
        const account = loadAccount()
        if (!account) throw new Error('WeChat not logged in — restart the channel to scan QR code')

        const contextToken = contextTokenStore.get(chatId)
        if (!contextToken) throw new Error(`No context token for ${chatId} — user must send a message first`)

        const plainText = stripMarkdown(text)
        const chunks = chunk(plainText, TEXT_CHUNK_LIMIT)
        const sentIds: string[] = []

        // WECHAT-FIX1：段间 900ms 延时（避开 BOT 通道限流）；单段失败重试 1 次，
        // 仍失败即抛（上层报 failed，不静默吞段）
        for (let ci = 0; ci < chunks.length; ci++) {
          if (ci > 0) await new Promise(r => setTimeout(r, CHUNK_SEND_DELAY_MS))
          let lastErr: unknown
          let sent = false
          for (let attempt = 1; attempt <= CHUNK_SEND_MAX_ATTEMPTS; attempt++) {
            try {
              const id = await ilinkSendMessage({
                baseUrl: account.baseUrl,
                token: account.token,
                to: chatId,
                text: chunks[ci],
                contextToken,
              })
              sentIds.push(id)
              sent = true
              break
            } catch (err) {
              lastErr = err
              if (attempt < CHUNK_SEND_MAX_ATTEMPTS) {
                await new Promise(r => setTimeout(r, RETRY_DELAY_MS))
              }
            }
          }
          if (!sent) {
            throw new Error(`chunk ${ci + 1}/${chunks.length} failed after ${CHUNK_SEND_MAX_ATTEMPTS} attempts: ${lastErr instanceof Error ? lastErr.message : String(lastErr)}`)
          }
        }

        const result = sentIds.length === 1
          ? `sent (id: ${sentIds[0]})`
          : `sent ${sentIds.length} parts`
        return { content: [{ type: 'text', text: result }] }
      }
      default:
        return { content: [{ type: 'text', text: `unknown tool: ${req.params.name}` }], isError: true }
    }
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err)
    return { content: [{ type: 'text', text: `${req.params.name} failed: ${msg}` }], isError: true }
  }
})

// ── Connect MCP ─────────────────────────────────────────────────────────────

await mcp.connect(new StdioServerTransport())

// ── Shutdown ────────────────────────────────────────────────────────────────

let shuttingDown = false
function shutdown(): void {
  if (shuttingDown) return
  shuttingDown = true
  process.stderr.write('wechat channel: shutting down\n')
  setTimeout(() => process.exit(0), 2000)
}
process.stdin.on('end', shutdown)
process.stdin.on('close', shutdown)
process.on('SIGTERM', shutdown)
process.on('SIGINT', shutdown)

process.on('unhandledRejection', err => {
  process.stderr.write(`wechat channel: unhandled rejection: ${err}\n`)
})
process.on('uncaughtException', err => {
  process.stderr.write(`wechat channel: uncaught exception: ${err}\n`)
})

// ── Wait for Login ──────────────────────────────────────────────────────────

let account = loadAccount()

if (!account) {
  process.stderr.write(
    'wechat channel: no account found. Run /wechat:configure to scan QR code.\n' +
    'wechat channel: waiting for account.json...\n',
  )
  // Poll for account.json every 3 seconds until it appears
  while (!account && !shuttingDown) {
    await new Promise(r => setTimeout(r, 3000))
    account = loadAccount()
  }
  if (!account) process.exit(0)
  process.stderr.write('wechat channel: account.json detected!\n')
}

process.stderr.write(`wechat channel: logged in as botId=${account.botId}, owner=${account.userId ?? 'unknown'}\n`)
process.stderr.write(`wechat channel: starting long-poll...\n`)

// ── Message Loop ────────────────────────────────────────────────────────────

let getUpdatesBuf = loadSyncBuf()
let consecutiveFailures = 0
let nextTimeoutMs = LONG_POLL_TIMEOUT_MS

while (!shuttingDown) {
  try {
    const resp = await ilinkGetUpdates({
      baseUrl: account.baseUrl,
      token: account.token,
      getUpdatesBuf,
      timeoutMs: nextTimeoutMs,
    })

    if (resp.longpolling_timeout_ms != null && resp.longpolling_timeout_ms > 0) {
      nextTimeoutMs = resp.longpolling_timeout_ms
    }

    const isApiError =
      (resp.ret !== undefined && resp.ret !== 0) ||
      (resp.errcode !== undefined && resp.errcode !== 0)

    if (isApiError) {
      // Session expired (-14)
      if (resp.errcode === -14 || resp.ret === -14) {
        process.stderr.write('wechat channel: session expired, need re-login. Exiting.\n')
        process.exit(1)
      }

      consecutiveFailures++
      process.stderr.write(
        `wechat channel: getUpdates error ret=${resp.ret} errcode=${resp.errcode} (${consecutiveFailures}/${MAX_CONSECUTIVE_FAILURES})\n`,
      )
      if (consecutiveFailures >= MAX_CONSECUTIVE_FAILURES) {
        consecutiveFailures = 0
        await new Promise(r => setTimeout(r, BACKOFF_DELAY_MS))
      } else {
        await new Promise(r => setTimeout(r, RETRY_DELAY_MS))
      }
      continue
    }

    consecutiveFailures = 0

    if (resp.get_updates_buf) {
      saveSyncBuf(resp.get_updates_buf)
      getUpdatesBuf = resp.get_updates_buf
    }

    const msgs = resp.msgs ?? []
    for (const msg of msgs) {
      const senderId = msg.from_user_id ?? ''
      if (!senderId) continue

      // Skip bot's own messages
      if (msg.message_type === 2) continue

      const text = extractText(msg)
      const media = getMediaInfo(msg)
      const body = text || media?.info || ''
      if (!body) continue

      // Cache context token
      if (msg.context_token) {
        contextTokenStore.set(senderId, msg.context_token)
      }

      // Send typing indicator
      const typingTicket = await getTypingTicket(account, senderId, msg.context_token)
      if (typingTicket) {
        void ilinkSendTyping({
          baseUrl: account.baseUrl,
          token: account.token,
          userId: senderId,
          typingTicket,
          status: 1, // TYPING
        })
      }

      // Forward to Claude via MCP notification
      const meta: Record<string, string> = {
        chat_id: senderId,
        user: senderId,
        ts: new Date((msg.create_time_ms ?? 0)).toISOString(),
      }
      if (msg.message_id != null) meta.message_id = String(msg.message_id)
      if (media) meta.media_type = media.kind

      mcp.notification({
        method: 'notifications/claude/channel',
        params: { content: body, meta },
      }).catch(err => {
        process.stderr.write(`wechat channel: failed to deliver to Claude: ${err}\n`)
      })
    }
  } catch (err) {
    if (shuttingDown) break
    consecutiveFailures++
    process.stderr.write(`wechat channel: poll error (${consecutiveFailures}): ${err}\n`)
    if (consecutiveFailures >= MAX_CONSECUTIVE_FAILURES) {
      consecutiveFailures = 0
      await new Promise(r => setTimeout(r, BACKOFF_DELAY_MS))
    } else {
      await new Promise(r => setTimeout(r, RETRY_DELAY_MS))
    }
  }
}
