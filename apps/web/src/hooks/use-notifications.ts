import type { Client, Clock, Notification, NotificationListResult } from "@kinby/contract"
import { useCallback, useEffect, useRef } from "react"

import { useRead } from "@/hooks/use-read"
import type { TurnBlock } from "@/lib/timeline"

/**
 * How often the panel reads the thread's notifications again while one is still sending or the
 * hub has yet to apply an answer to one.
 */
const SETTLING_READ_MS = 5_000

/** How many of the thread's newest notifications a read holds, and how many more each ask adds. */
const PAGE = 50

/** What the hub keeps of the thread's notifications, and the answer to one of their options. */
export interface Notifications {
  kept: ReadonlyMap<string, Notification>
  answer: (notificationId: string, option: string) => Promise<void>
  /** Read a page more of the older ones, while the hub keeps older ones than those read. */
  older: (() => void) | undefined
}

/**
 * The hub's newest notifications of the thread, read again whenever the hub takes another or a
 * turn starts, and while one is still settling. Each ask for older ones reads a page more.
 */
export function useNotifications(
  hub: Pick<Client, "call">,
  clock: Clock,
  threadId: string,
  turns: TurnBlock[],
): Notifications {
  const accepted = turns
    .flatMap((turn) => turn.steps)
    .filter((step) => step.kind === "notification" && step.hub?.kind === "accepted").length
  // A ref, so asking for older ones keeps what was read until the larger read returns.
  const pages = useRef(1)
  const list = useCallback(
    async (): Promise<NotificationListResult> =>
      accepted === 0
        ? { notifications: [], cursor: null }
        : await hub.call("notification.list", { thread_id: threadId, limit: PAGE * pages.current }),
    [hub, threadId, accepted],
  )
  const { value: read, readAgain } = useRead(list, clock)
  const value = read?.notifications
  // A reply through a channel shows in the thread only as the turn it starts.
  const started = turns.length
  const seen = useRef(started)
  useEffect(() => {
    if (seen.current === started) return
    seen.current = started
    readAgain()
  }, [started, readAgain])
  const settling =
    value?.some(
      (kept) =>
        kept.deliveries.some((delivery) => delivery.status === "pending") ||
        kept.replies.some((reply) => reply.status === "received"),
    ) ?? false
  useEffect(
    () => (settling ? clock.after(SETTLING_READ_MS, readAgain) : undefined),
    [settling, value, clock, readAgain],
  )
  const answer = async (notificationId: string, option: string) => {
    await hub.call("notification.reply", { notification_id: notificationId, option })
    readAgain()
  }
  const older = () => {
    pages.current += 1
    readAgain()
  }
  return {
    kept: new Map((value ?? []).map((kept) => [kept.notification_id, kept])),
    answer,
    older: read?.cursor == null ? undefined : older,
  }
}
