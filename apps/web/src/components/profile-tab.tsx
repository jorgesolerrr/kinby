import type { Clock, InstanceClient } from "@kinby/contract"
import { useCallback, useEffect, useId, useState } from "react"

import { Failure, StaleAlert } from "@/components/config-alerts"
import { Button } from "@/components/ui/button"
import { Field, FieldDescription, FieldLabel } from "@/components/ui/field"
import { Skeleton } from "@/components/ui/skeleton"
import { Spinner } from "@/components/ui/spinner"
import { Textarea } from "@/components/ui/textarea"
import { usePace } from "@/hooks/use-pace"
import { lastChanged } from "@/lib/config-changes"
import { retried } from "@/lib/operation"
import { type OpenedProfile, openProfile, PROFILE_FILE, saveProfile, tokens } from "@/lib/profile"

type Caller = Pick<InstanceClient, "call">

/**
 * The profile as text, with about how many tokens it takes in every prompt. A save carries the
 * hash of what was read, so a save over a change made since is refused, and "Load theirs" reads
 * the profile again.
 */
export function ProfileTab({ client, clock }: { client: Caller; clock: Clock }) {
  const id = useId()
  const pacing = usePace(clock)
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
    () =>
      retried(() => openProfile(client), pacing).then(
        (read) => {
          show(read)
          setNotice(undefined)
          setFailure(undefined)
        },
        (error: unknown) => setFailure(error),
      ),
    [client, pacing, show],
  )
  useEffect(() => {
    void load()
  }, [load])

  if (opened === undefined) {
    return failure === undefined ? (
      <Skeleton className="h-72 w-full" />
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
    <div className="flex flex-col gap-3">
      {notice === "stale" && <StaleAlert file={PROFILE_FILE} onLoad={() => void load()} />}
      {failure !== undefined && <Failure error={failure} />}
      <Field>
        <FieldLabel htmlFor={id}>{PROFILE_FILE}</FieldLabel>
        <FieldDescription>
          It goes into every prompt, about {tokens(draft)} tokens.
        </FieldDescription>
        <Textarea
          id={id}
          className="min-h-72"
          value={draft}
          readOnly={saving}
          onChange={(event) => {
            setDraft(event.target.value)
            if (notice === "saved") setNotice(undefined)
          }}
        />
        <FieldDescription>{lastChanged(opened.lastChange)}</FieldDescription>
      </Field>
      <div className="flex items-center gap-3">
        <Button disabled={saving || draft === opened.profile.text} onClick={() => void save()}>
          {saving && <Spinner data-icon="inline-start" />}
          Save
        </Button>
        {notice === "saved" && (
          <span className="text-sm text-muted-foreground">Saved. It applies at the next turn.</span>
        )}
      </div>
    </div>
  )
}
