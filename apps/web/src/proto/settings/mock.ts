// PROTOTYPE: static mock hub data for /proto/settings. Throwaway.
import type { Avatar } from "@kinby/contract"

import type { HubPage } from "../shell/mock"

export type Section = HubPage

export const SECTIONS: { id: Section; label: string; hint: string }[] = [
  { id: "general", label: "General", hint: "Theme" },
  { id: "usage", label: "Usage", hint: "Turns, cost and plan limits" },
  { id: "secrets", label: "Hub secrets", hint: "Values you set once" },
  { id: "channels", label: "Channels", hint: "Where notifications go" },
  { id: "instances", label: "Instances", hint: "Removed instances" },
]

export interface SecretLink {
  instance: string
  field: string
}

export interface Secret {
  name: string
  links: SecretLink[]
}

export const SECRETS: Secret[] = [
  {
    name: "ANTHROPIC_API_KEY",
    links: [
      { instance: "kin", field: "api_key" },
      { instance: "scout", field: "api_key" },
      { instance: "cec-coder", field: "api_key" },
    ],
  },
  {
    name: "GITHUB_TOKEN",
    links: [
      { instance: "cec-coder", field: "github_token" },
      { instance: "coder", field: "github_token" },
    ],
  },
  { name: "BRAVE_SEARCH_API_KEY", links: [{ instance: "scout", field: "brave_api_key" }] },
  { name: "TELEGRAM_BOT_TOKEN", links: [] },
  {
    name: "GOOGLE_OAUTH_CLIENT_SECRET",
    links: [{ instance: "kin", field: "google_client_secret" }],
  },
  { name: "OPENAI_API_KEY", links: [] },
  { name: "STRIPE_RESTRICTED_KEY", links: [{ instance: "ledger", field: "stripe_key" }] },
  { name: "NOTION_TOKEN", links: [{ instance: "kin", field: "notion_token" }] },
]

/** The instances running now, so Replace offers to recreate them. */
export const RUNNING = new Set(["kin", "scout", "cec-coder"])

export interface ChannelKind {
  kind: string
  pairs: boolean
  fields: { name: string; label: string; secret: boolean; placeholder?: string }[]
}

export const CHANNEL_KINDS: ChannelKind[] = [
  {
    kind: "telegram",
    pairs: true,
    fields: [{ name: "bot_token", label: "Bot token", secret: true, placeholder: "123456:ABC…" }],
  },
  {
    kind: "discord",
    pairs: true,
    fields: [
      { name: "application_id", label: "Application ID", secret: false },
      { name: "public_key", label: "Public key", secret: false },
      { name: "bot_token", label: "Bot token", secret: true },
    ],
  },
  {
    kind: "ntfy",
    pairs: false,
    fields: [
      { name: "server", label: "Server", secret: false, placeholder: "https://ntfy.sh" },
      { name: "topic", label: "Topic", secret: false },
    ],
  },
]

export interface Channel {
  id: string
  name: string
  kind: string
  enabled: boolean
  paired: boolean
  pairingCode?: string
  pairingUntil?: string
  lastSent?: string
  lastFailure?: { at: string; error: string }
  muted: string[]
}

export const CHANNELS: Channel[] = [
  {
    id: "ch1",
    name: "Jorge's Telegram",
    kind: "telegram",
    enabled: true,
    paired: true,
    lastSent: "Today, 9:02",
    muted: ["ledger"],
  },
  {
    id: "ch2",
    name: "Home Discord",
    kind: "discord",
    enabled: true,
    paired: false,
    pairingCode: "KIN-4821",
    pairingUntil: "Today, 18:40",
    muted: [],
  },
  {
    id: "ch3",
    name: "Phone (ntfy)",
    kind: "ntfy",
    enabled: true,
    paired: false,
    lastSent: "Yesterday, 22:15",
    lastFailure: { at: "Today, 7:30", error: "ntfy.sh answered 429 Too Many Requests" },
    muted: ["scout", "coder"],
  },
  {
    id: "ch4",
    name: "Work Telegram",
    kind: "telegram",
    enabled: false,
    paired: true,
    lastSent: "Oct 12, 16:20",
    muted: [],
  },
]

export interface Removed {
  id: string
  name: string
  avatar: Avatar
  removed: string
  directories: string[]
  volumes: string[]
  retained: string[]
}

export const REMOVED: Removed[] = [
  {
    id: "tester",
    name: "Tester",
    avatar: { shape: "circle", color: "gray" },
    removed: "3 days ago",
    directories: ["/srv/kinby/instances/tester/home", "/srv/kinby/instances/tester/config"],
    volumes: ["kinby-tester-browser", "kinby-tester-memory"],
    retained: [],
  },
  {
    id: "old-scout",
    name: "Scout (March)",
    avatar: { shape: "squircle", color: "blue" },
    removed: "Oct 2",
    directories: ["/srv/kinby/instances/old-scout/home"],
    volumes: ["kinby-old-scout-memory"],
    retained: ["/home/jorge/research → /workspace/research"],
  },
]

export const NAMES: Record<string, string> = {
  kin: "Kin",
  scout: "Scout",
  coder: "Coder",
  "cec-coder": "cec-coder",
  ledger: "Ledger",
}

export const PLANS = [
  { label: "Claude Max · 5-hour window", used: 42, resets: "resets in 2 h 10 min" },
  { label: "Claude Max · weekly", used: 61, resets: "resets Monday" },
  { label: "ChatGPT Pro · weekly", used: 18, resets: "resets Thursday" },
]

export const TOTALS = [
  { title: "Turns", value: "412", note: "6 failed · 3 interrupted" },
  { title: "API cost", value: "$46.70", note: "8.2M in · 640K out" },
  { title: "Plan runs", value: "289", note: "Claude 241 · ChatGPT 48" },
  { title: "Factory runs", value: "17", note: "3.1M tokens · $12.40" },
]

export const USAGE_ROWS: {
  instance: string
  turns?: number
  cost?: string
  claude?: number
  chatgpt?: number
  factory?: number
  notCounted?: string
}[] = [
  { instance: "Kin", turns: 182, cost: "$14.20", claude: 121, chatgpt: 30, factory: 0 },
  { instance: "Scout", turns: 64, cost: "$9.80", claude: 52, chatgpt: 18, factory: 0 },
  { instance: "cec-coder", turns: 141, cost: "$22.40", claude: 64, chatgpt: 0, factory: 17 },
  { instance: "Coder", turns: 25, cost: "$0.30", claude: 4, chatgpt: 0, factory: 0 },
  { instance: "Ledger", notCounted: "stopped, so the hub could not ask it" },
]

/** Turns per day over the last 7 days, per instance, for a small stacked bar. */
export const DAILY = [
  { day: "Mon", kin: 22, scout: 8, coder: 20 },
  { day: "Tue", kin: 31, scout: 12, coder: 18 },
  { day: "Wed", kin: 25, scout: 6, coder: 30 },
  { day: "Thu", kin: 28, scout: 10, coder: 24 },
  { day: "Fri", kin: 34, scout: 14, coder: 26 },
  { day: "Sat", kin: 18, scout: 4, coder: 9 },
  { day: "Sun", kin: 24, scout: 10, coder: 14 },
]
