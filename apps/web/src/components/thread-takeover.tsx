import type { Client, InstanceClient } from "@kinby/contract"
import { useCallback, useId, useRef, useState } from "react"

import { LiveBrowser, type LiveBrowserInput } from "@/components/live-browser"
import { Button } from "@/components/ui/button"
import { Card, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card"
import { Field, FieldDescription, FieldLabel } from "@/components/ui/field"
import { Input } from "@/components/ui/input"
import { Spinner } from "@/components/ui/spinner"
import { reason } from "@/lib/operation"
import type { ParkedTakeover } from "@/lib/timeline"
import { CircleStopIcon, FlagIcon, GlobeIcon, MousePointerClickIcon } from "lucide-react"

/**
 * The takeover a turn is parked on, as a card at the end of the turn: why the agent asks, the
 * button that opens its browser in the pane beside the thread, and Stop the turn.
 */
export function TakeoverCard({
  client,
  threadId,
  takeover,
  name,
  paneOpen,
  onOpen,
}: {
  client: Pick<InstanceClient, "call">
  threadId: string
  takeover: ParkedTakeover
  /** What the instance is called. */
  name: string
  /** Whether the takeover's pane is open beside the thread. */
  paneOpen: boolean
  onOpen: () => void
}) {
  const [failure, setFailure] = useState<string>()
  const [stopping, setStopping] = useState(false)
  const stop = async () => {
    setStopping(true)
    try {
      await client.call("thread.turn.interrupt", { thread_id: threadId })
    } catch (error) {
      setFailure(reason(error))
      setStopping(false)
    }
  }

  return (
    <section aria-label="Take over the browser">
      <Card size="sm" variant="attention">
        <CardHeader>
          <CardTitle>
            <span className="flex items-center gap-2 wrap-anywhere">
              <MousePointerClickIcon aria-hidden className="size-4 shrink-0 text-ember" />
              {name} needs you in the browser
            </span>
          </CardTitle>
          <CardDescription className="wrap-anywhere">{takeover.reason}</CardDescription>
        </CardHeader>
        {/* Below md the buttons grow to a 44 px tap target. */}
        <CardFooter>
          <div className="flex w-full flex-col gap-2">
            {failure !== undefined && (
              <p role="alert" className="text-sm text-destructive">
                {failure}
              </p>
            )}
            <div className="flex w-full flex-wrap items-center gap-2">
              <Button className="max-md:h-11" onClick={onOpen}>
                <GlobeIcon data-icon="inline-start" />
                {paneOpen ? "Show the browser" : `Open ${takeover.site ?? "the browser"}`}
              </Button>
              <Button
                variant="ghost"
                className="ml-auto max-md:h-11"
                disabled={stopping}
                onClick={() => void stop()}
              >
                {stopping ? (
                  <Spinner data-icon="inline-start" />
                ) : (
                  <CircleStopIcon data-icon="inline-start" />
                )}
                Stop the turn
              </Button>
            </div>
          </div>
        </CardFooter>
      </Card>
    </section>
  )
}

/**
 * The browser of the takeover a turn is parked on, as the pane beside the thread holds it: why the
 * agent asks, then its page live, which takes the user's mouse and keyboard. The hub keeps the
 * page, so the user drives it there. Finish resumes the turn with the page as the user left it,
 * saved first as a profile when the user names one.
 */
export function TakeoverPane({
  client,
  hub,
  instanceId,
  threadId,
  turnId,
  takeover,
  name,
}: {
  client: Pick<InstanceClient, "call">
  hub: Pick<Client, "call" | "subscribe">
  instanceId: string
  threadId: string
  turnId: string
  takeover: ParkedTakeover
  /** What the instance is called. */
  name: string
}) {
  const page = { instance_id: instanceId, turn_id: turnId }
  const ids = { ...page, thread_id: threadId, takeover_id: takeover.takeoverId }
  const watch = useCallback(
    () => hub.subscribe("thread.takeover.start", { instance_id: instanceId, turn_id: turnId }),
    [hub, instanceId, turnId],
  )
  const profileId = useId()
  const [profile, setProfile] = useState("")
  const [failure, setFailure] = useState<string>()
  const [answering, setAnswering] = useState<"finish" | "stop">()

  const live = useRef<LiveBrowserInput>(null)
  const answer = async (pressed: "finish" | "stop", work: () => Promise<unknown>) => {
    setAnswering(pressed)
    try {
      await work()
    } catch (error) {
      setFailure(reason(error))
      setAnswering(undefined)
    }
  }
  const busy = answering !== undefined
  const profileName = profile.trim()

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      {/* Nothing in the body shrinks, so a page taller than the pane scrolls rather than clips. */}
      <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto p-4 *:shrink-0">
        <p className="text-sm wrap-anywhere text-muted-foreground">{takeover.reason}</p>
        <LiveBrowser
          ref={live}
          watch={watch}
          send={(input) => hub.call("thread.takeover.input", { ...page, input })}
          // Input after Finish or Stop would reach a turn that already moved on.
          disabled={busy}
        />
        {failure !== undefined && (
          <p role="alert" className="text-sm text-destructive">
            {failure}
          </p>
        )}
      </div>
      <div className="flex flex-wrap items-end justify-between gap-3 border-t p-4">
        <Field className="max-w-64">
          <FieldLabel htmlFor={profileId}>Save as a profile</FieldLabel>
          <Input
            id={profileId}
            value={profile}
            placeholder="LinkedIn"
            readOnly={busy}
            onChange={(event) => setProfile(event.target.value)}
          />
          <FieldDescription>
            Name the sign-in to let {name} open the site signed in later.
          </FieldDescription>
        </Field>
        <div className="flex items-center gap-2">
          <Button
            variant="ghost"
            disabled={busy}
            onClick={() =>
              void answer("stop", () =>
                client.call("thread.turn.interrupt", { thread_id: threadId }),
              )
            }
          >
            {answering === "stop" ? (
              <Spinner data-icon="inline-start" />
            ) : (
              <CircleStopIcon data-icon="inline-start" />
            )}
            Stop the turn
          </Button>
          <Button
            disabled={busy}
            onClick={() =>
              void answer("finish", async () => {
                // What the user typed last reaches the page before the turn resumes.
                await live.current?.drained()
                return hub.call(
                  "thread.takeover.finish",
                  profileName === "" ? ids : { ...ids, profile: profileName },
                )
              })
            }
          >
            {answering === "finish" ? (
              <Spinner data-icon="inline-start" />
            ) : (
              <FlagIcon data-icon="inline-start" />
            )}
            Finish
          </Button>
        </div>
      </div>
    </div>
  )
}
