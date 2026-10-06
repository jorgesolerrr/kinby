import type {
  Client,
  Clock,
  InstanceSummary,
  PackageSummary,
  PackageTemplateOlder,
} from "@kinby/contract"
import { useCallback, useId, useState } from "react"

import { OperationSteps } from "@/components/operation-steps"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import { Field, FieldDescription, FieldLabel } from "@/components/ui/field"
import { Input } from "@/components/ui/input"
import { Item, ItemContent, ItemDescription, ItemGroup, ItemTitle } from "@/components/ui/item"
import { Spinner } from "@/components/ui/spinner"
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group"
import { useFollowing } from "@/hooks/use-following"
import { packageName } from "@/lib/instances"
import { followUpdate, type Updating } from "@/lib/update"
import { CircleCheckIcon, CircleXIcon, TriangleAlertIcon } from "lucide-react"

type InstanceNotice = InstanceSummary["notices"][number]

const NOTICE_TITLES: Record<InstanceNotice["code"], string> = {
  revision_behind: "Behind the hub",
  package_template_older: "The template is older",
}

/** What "Update core" moves the instance to: the hub's own revision, or a ref the user types. */
type Target = "hub" | "ref"

const TARGETS: Record<Target, string> = { hub: "The hub's revision", ref: "Another ref" }

/**
 * The instance's package and the versions it came from, what the hub says about them, and
 * "Update core". `caller` reaches the hub, and `onChanged` hears an update succeed.
 */
export function PackageSection({
  caller,
  clock,
  instance,
  onChanged,
}: {
  caller: Pick<Client, "call">
  clock: Clock
  instance: InstanceSummary
  onChanged: () => void
}) {
  const older = instance.notices.find(
    (notice): notice is PackageTemplateOlder => notice.code === "package_template_older",
  )
  return (
    <div className="flex max-w-2xl flex-col gap-6">
      {instance.notices.map((notice) => (
        <Notice key={notice.code} notice={notice} />
      ))}
      <ItemGroup aria-label="Package and version">
        <Fact label="Package">{packageName(instance.package)}</Fact>
        {instance.package != null && (
          <>
            <Fact label="Set up from">{older?.initialized_version ?? "The installed version"}</Fact>
            <Fact label="Installed">{installed(instance.package, older)}</Fact>
          </>
        )}
        <Fact label="kinby">{instance.source_revision.slice(0, 7)}</Fact>
      </ItemGroup>
      <UpdateCore caller={caller} clock={clock} instance={instance} onChanged={onChanged} />
    </div>
  )
}

function Notice({ notice }: { notice: InstanceNotice }) {
  const titleId = useId()
  return (
    <Alert aria-labelledby={titleId}>
      <TriangleAlertIcon />
      <AlertTitle id={titleId}>{NOTICE_TITLES[notice.code]}</AlertTitle>
      <AlertDescription>{notice.message}</AlertDescription>
    </Alert>
  )
}

function Fact({ label, children }: { label: string; children: string }) {
  return (
    <Item render={<li />} aria-label={label} variant="outline" size="sm">
      <ItemContent>
        <ItemTitle>{label}</ItemTitle>
        <ItemDescription>{children}</ItemDescription>
      </ItemContent>
    </Item>
  )
}

/** A pinned commit reads as its short SHA, after the version it installs when the hub knows it. */
function installed(summary: PackageSummary, older: PackageTemplateOlder | undefined): string {
  const { version } = summary
  if (typeof version === "string") return version
  const commit = `commit ${version.sha.slice(0, 7)}`
  return older === undefined ? commit : `${older.installed_version}, ${commit}`
}

function UpdateCore({
  caller,
  clock,
  instance,
  onChanged,
}: {
  caller: Pick<Client, "call">
  clock: Clock
  instance: InstanceSummary
  onChanged: () => void
}) {
  const refId = useId()
  const [target, setTarget] = useState<Target>("hub")
  const [ref, setRef] = useState("")
  const [request, setRequest] = useState<{ revision: string }>()
  const behind = instance.notices.find((notice) => notice.code === "revision_behind")
  const hubRevision = behind?.hub_revision
  const update = useCallback(
    ({ revision }: { revision: string }, report: (updating: Updating) => void) =>
      followUpdate(
        caller,
        { instance_id: instance.instance_id, revision },
        (updating) => {
          report(updating)
          if (updating.state === "updated") onChanged()
        },
        clock,
      ),
    [caller, clock, instance.instance_id, onChanged],
  )
  const updating = useFollowing(request, update)
  const busy = request !== undefined && (updating === undefined || updating.state === "updating")
  // The hub builds its own checkout from HEAD, which is the revision a notice names.
  const revision = target === "hub" ? (hubRevision ?? "HEAD") : ref.trim()

  return (
    <section className="flex flex-col gap-3">
      <h2 className="font-medium">Update core</h2>
      <p className="text-sm text-muted-foreground">
        Moves the instance onto the kinby a revision builds. The hub checks the new image before it
        stops anything, so a failed check leaves the instance running.
      </p>
      <ToggleGroup
        aria-label="Revision"
        variant="outline"
        value={[target]}
        onValueChange={([picked]) => {
          if (picked === "hub" || picked === "ref") setTarget(picked)
        }}
      >
        {Object.entries(TARGETS).map(([value, label]) => (
          <ToggleGroupItem key={value} value={value} disabled={busy}>
            {label}
          </ToggleGroupItem>
        ))}
      </ToggleGroup>
      {target === "hub" ? (
        hubRevision !== undefined && (
          <p className="text-sm text-muted-foreground">{hubRevision.slice(0, 7)}</p>
        )
      ) : (
        <Field>
          <FieldLabel htmlFor={refId}>Ref</FieldLabel>
          <Input
            id={refId}
            placeholder="v0.3.0"
            value={ref}
            readOnly={busy}
            onChange={(event) => setRef(event.target.value)}
          />
          <FieldDescription>A tag, a branch, or a commit in the hub's checkout.</FieldDescription>
        </Field>
      )}
      <div>
        <Button disabled={busy || revision === ""} onClick={() => setRequest({ revision })}>
          {busy && <Spinner data-icon="inline-start" />}
          Update core
        </Button>
      </div>
      {updating !== undefined && <UpdateProgress updating={updating} />}
    </section>
  )
}

function UpdateProgress({ updating }: { updating: Updating }) {
  const titleId = useId()
  return (
    <>
      <OperationSteps label="Update steps" steps={updating.steps} />
      {updating.state === "updated" && (
        <Alert>
          <CircleCheckIcon />
          <AlertTitle>Updated. The instance runs the new core.</AlertTitle>
        </Alert>
      )}
      {updating.state === "blocked" && (
        <Alert variant="destructive" aria-labelledby={titleId}>
          <CircleXIcon />
          <AlertTitle id={titleId}>The update stopped before it began</AlertTitle>
          <AlertDescription>
            <p>The instance keeps running on its current core.</p>
            <ul className="list-disc pl-4">
              {updating.problems.map((problem) => (
                <li key={problem}>{problem}</li>
              ))}
            </ul>
          </AlertDescription>
        </Alert>
      )}
      {updating.state === "failed" && (
        <Alert variant="destructive" aria-labelledby={titleId}>
          <CircleXIcon />
          <AlertTitle id={titleId}>The update failed</AlertTitle>
          <AlertDescription>{updating.detail}</AlertDescription>
        </Alert>
      )}
    </>
  )
}
