import type { Clock, InstanceClient } from "@kinby/contract"
import { useCallback, useEffect, useId, useState } from "react"

import { Failure, StaleAlert } from "@/components/config-alerts"
import { Button } from "@/components/ui/button"
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible"
import { Field, FieldDescription, FieldLabel } from "@/components/ui/field"
import {
  Item,
  ItemActions,
  ItemContent,
  ItemDescription,
  ItemMedia,
  ItemTitle,
} from "@/components/ui/item"
import { Skeleton } from "@/components/ui/skeleton"
import { Spinner } from "@/components/ui/spinner"
import { Textarea } from "@/components/ui/textarea"
import { usePace } from "@/hooks/use-pace"
import { lastChanged } from "@/lib/config-changes"
import { type Pace, pace, retried } from "@/lib/operation"
import { type OpenedProfile, openProfile, PROFILE_FILE, saveProfile, tokens } from "@/lib/profile"
import { UserRoundIcon } from "lucide-react"

type Caller = Pick<InstanceClient, "call">

/** The editor takes the focus when it unfolds. */
const focus = (element: HTMLElement | null) => element?.focus()

/**
 * The profile as one row, its file and about how many tokens it takes in every prompt, unfolding
 * to its text. A save carries the hash of what was read, so a save over a change made since is
 * refused, and "Load theirs" reads the profile again.
 */
export function ProfileRow({ client, clock }: { client: Caller; clock: Clock }) {
  const id = useId()
  const pacing = usePace(clock)
  const [open, setOpen] = useState(false)
  const [opened, setOpened] = useState<OpenedProfile>()
  const [draft, setDraft] = useState("")
  const [saving, setSaving] = useState(false)
  const [notice, setNotice] = useState<"saved" | "stale">()
  const [failure, setFailure] = useState<unknown>()

  const show = useCallback((read: OpenedProfile) => {
    setOpened(read)
    setDraft(read.profile.text)
  }, [])
  const load = useCallback(
    (pacing: Pace) =>
      retried(() => openProfile(client), pacing).then(
        (read) => {
          show(read)
          setNotice(undefined)
          setFailure(undefined)
        },
        (error: unknown) => setFailure(error),
      ),
    [client, show],
  )
  useEffect(() => {
    const pacing = pace(clock)
    void load(pacing)
    return pacing.stop
  }, [clock, load])

  if (opened === undefined) {
    return failure === undefined ? (
      <Skeleton className="h-14 w-full" />
    ) : (
      <Failure error={failure} />
    )
  }
  const save = async () => {
    setSaving(true)
    setFailure(undefined)
    try {
      const saved = await saveProfile(client, draft, opened.profile.hash)
      if (saved.state === "saved") show(saved.opened)
      setNotice(saved.state)
    } catch (error) {
      setFailure(error)
    } finally {
      setSaving(false)
    }
  }

  return (
    <Collapsible open={open} onOpenChange={setOpen}>
      <Item size="row">
        <ItemMedia variant="tile">
          <UserRoundIcon />
        </ItemMedia>
        <ItemContent className="min-w-0">
          <ItemTitle>{PROFILE_FILE}</ItemTitle>
          <ItemDescription lines="one">
            About {tokens(draft)} tokens in every prompt
          </ItemDescription>
        </ItemContent>
        <ItemActions>
          <CollapsibleTrigger render={<Button variant="outline" size="sm" />}>
            {open ? "Close" : "Edit"}
          </CollapsibleTrigger>
        </ItemActions>
      </Item>
      <CollapsibleContent>
        <div className="flex flex-col gap-3 pb-4 pl-11">
          {notice === "stale" && (
            <StaleAlert file={PROFILE_FILE} onLoad={() => void load(pacing)} />
          )}
          {failure !== undefined && <Failure error={failure} />}
          <Field>
            <FieldLabel htmlFor={id} className="sr-only">
              {PROFILE_FILE}
            </FieldLabel>
            <Textarea
              id={id}
              ref={focus}
              className="min-h-72"
              value={draft}
              readOnly={saving}
              onChange={(event) => {
                setDraft(event.target.value)
                if (notice === "saved") setNotice(undefined)
              }}
            />
            <FieldDescription>
              {lastChanged(opened.lastChange, opened.profile.hash)}
            </FieldDescription>
          </Field>
          <div className="flex items-center gap-3">
            <Button disabled={saving || draft === opened.profile.text} onClick={() => void save()}>
              {saving && <Spinner data-icon="inline-start" />}
              Save
            </Button>
            {notice === "saved" && (
              <span className="text-sm text-muted-foreground">
                Saved. It applies at the next turn.
              </span>
            )}
          </div>
        </div>
      </CollapsibleContent>
    </Collapsible>
  )
}
