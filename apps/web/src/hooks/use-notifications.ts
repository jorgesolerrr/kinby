import type { Client, Clock, Notification } from "@kinby/contract"
import { useCallback, useEffect, useRef } from "react"

import { useRead } from "@/hooks/use-read"
import type { TurnBlock } from "@/lib/timeline"

/**
 * How often the panel reads the thread's notifications again while one is still sending or the
 * hub has yet to apply an answer to one.
 */
const SETTLING_READ_MS = 5_000

/** What the hub keeps of the thread's notifications, and the answer to one of their options. */
export interface Notifications {
  kept: ReadonlyMap<string, Notification>
  answer: (notificationId: string, option: string) => Promise<void>
}

/**
 * The hub's notifications of the thread, read again whenever the hub takes another or a turn
 * starts, and while one is still settling.
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
  const list = useCallback(
    async () =>
      accepted === 0
        ? []
        : (await hub.call("notification.list", { thread_id: threadId })).notifications,
    [hub, threadId, accepted],
  )
  const { value, readAgain } = useRead(list, clock)
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
  return {
    kept: new Map((value ?? []).map((kept) => [kept.notification_id, kept])),
    answer,
  }
}
