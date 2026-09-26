import { browserClock } from "@kinby/contract"
import type {
  Client,
  Clock,
  CuratedPackage,
  OperationState,
  OperationStep,
  PackageSelection,
  SetupField,
} from "@kinby/contract"
import { useEffect, useState } from "react"
import type * as React from "react"

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Item,
  ItemActions,
  ItemContent,
  ItemDescription,
  ItemGroup,
  ItemMedia,
  ItemTitle,
} from "@/components/ui/item"
import { Spinner } from "@/components/ui/spinner"
import { followPreparation, type Preparation } from "@/lib/preparation"
import {
  CircleCheckIcon,
  CircleXIcon,
  CodeIcon,
  PackageIcon,
  SparklesIcon,
  type LucideIcon,
} from "lucide-react"

const STATES: Record<OperationState, { label: string; icon: React.ReactNode }> = {
  pending: { label: "Waiting", icon: <Spinner /> },
  running: { label: "Running", icon: <Spinner /> },
  succeeded: { label: "Done", icon: <CircleCheckIcon /> },
  failed: { label: "Failed", icon: <CircleXIcon /> },
}

/** The icons the curated entries name. An icon the app does not know draws as a package. */
const ICONS: Record<string, LucideIcon> = { code: CodeIcon }

/** One click on a card. A new pick of the same card prepares it again. */
type CardPick = { selection: PackageSelection | null }

type CuratedList =
  | { state: "loading" }
  | { state: "failed"; detail: string }
  | { state: "loaded"; packages: CuratedPackage[] }

/** Create an instance. Its package step picks what the instance starts from and prepares it. */
export function CreateWizard({
  caller,
  clock = browserClock,
}: {
  caller: Pick<Client, "call">
  clock?: Clock
}) {
  const curated = useCuratedList(caller)
  const [pick, setPick] = useState<CardPick>()
  const preparation = usePreparation(caller, pick, clock)
  // A card being prepared, or already prepared, waits; any other card can still be picked.
  const busy = (selection: PackageSelection | null) =>
    pick?.selection === selection &&
    (preparation?.state === "preparing" || preparation?.state === "prepared")

  return (
    <div className="flex flex-col gap-6 p-6">
      <header className="flex flex-col gap-1">
        <h1 className="text-lg font-medium">New instance</h1>
        <p className="text-sm text-muted-foreground">
          Package: pick what the instance starts from.
        </p>
      </header>
      <ItemGroup aria-label="Packages" className="max-w-2xl">
        <PackageCard
          name="Vanilla"
          description="kinby's built-in defaults, with no package."
          icon={SparklesIcon}
          action="Prepare vanilla"
          disabled={busy(null)}
          onPick={() => setPick({ selection: null })}
        />
        {curated.state === "loaded" &&
          curated.packages.map((entry) => (
            <PackageCard
              key={entry.id}
              name={entry.display_name}
              description={entry.description}
              icon={ICONS[entry.icon] ?? PackageIcon}
              version={versionOf(entry.selection)}
              action={`Prepare ${entry.display_name}`}
              disabled={busy(entry.selection)}
              onPick={() => setPick({ selection: entry.selection })}
            />
          ))}
      </ItemGroup>
      {curated.state === "failed" && (
        <Alert variant="destructive" className="max-w-2xl">
          <CircleXIcon />
          <AlertTitle>The curated packages could not be listed</AlertTitle>
          <AlertDescription>{curated.detail}</AlertDescription>
        </Alert>
      )}
      {preparation !== undefined && <PreparationView preparation={preparation} />}
    </div>
  )
}

/** The packages the hub curates, read once when the wizard opens. */
function useCuratedList(caller: Pick<Client, "call">): CuratedList {
  const [curated, setCurated] = useState<CuratedList>({ state: "loading" })
  useEffect(() => {
    let current = true
    caller.call("package.list", {}).then(
      ({ packages }) => {
        if (current) setCurated({ state: "loaded", packages })
      },
      (error: unknown) => {
        if (current) {
          setCurated({
            state: "failed",
            detail: error instanceof Error ? error.message : String(error),
          })
        }
      },
    )
    return () => {
      current = false
    }
  }, [caller])
  return curated
}

/** The preparation of the picked card: nothing before a pick, then each poll of that pick. */
function usePreparation(
  caller: Pick<Client, "call">,
  pick: CardPick | undefined,
  clock: Clock,
): Preparation | undefined {
  const [followed, setFollowed] = useState<{ pick: CardPick; preparation: Preparation }>()
  useEffect(() => {
    if (pick === undefined) return
    return followPreparation(
      caller,
      pick.selection,
      (preparation) => {
        setFollowed({ pick, preparation })
      },
      clock,
    )
  }, [caller, pick, clock])
  if (pick === undefined) return undefined
  // The previous pick's report stays stored until this one answers.
  if (followed?.pick !== pick) return { state: "preparing", steps: [] }
  return followed.preparation
}

/** A pinned commit reads as its short SHA, an index version as itself. */
function versionOf(selection: PackageSelection): string {
  return typeof selection.version === "string"
    ? selection.version
    : selection.version.sha.slice(0, 7)
}

function PackageCard({
  name,
  description,
  icon: Icon,
  version,
  action,
  disabled,
  onPick,
}: {
  name: string
  description: string
  icon: LucideIcon
  version?: string
  action: string
  disabled: boolean
  onPick: () => void
}) {
  return (
    <Item render={<li />} aria-label={name} variant="outline">
      <ItemMedia variant="icon">
        <Icon />
      </ItemMedia>
      <ItemContent>
        <ItemTitle>
          {name}
          {version !== undefined && <Badge variant="outline">{version}</Badge>}
        </ItemTitle>
        <ItemDescription>{description}</ItemDescription>
      </ItemContent>
      <ItemActions>
        <Button disabled={disabled} onClick={onPick}>
          {action}
        </Button>
      </ItemActions>
    </Item>
  )
}

function PreparationView({ preparation }: { preparation: Preparation }) {
  const failedOutsideAStep =
    preparation.state === "failed" && !preparation.steps.some((step) => step.state === "failed")
  return (
    <section className="flex max-w-2xl flex-col gap-3">
      <h2 className="font-medium">Preparing the image</h2>
      <Steps steps={preparation.steps} />
      {failedOutsideAStep && (
        <Alert variant="destructive">
          <CircleXIcon />
          <AlertTitle>The preparation failed</AlertTitle>
          <AlertDescription>{preparation.detail}</AlertDescription>
        </Alert>
      )}
      {preparation.state === "prepared" && (
        <>
          <h2 className="font-medium">What it asks for</h2>
          <Fields fields={preparation.description.setup_fields} />
        </>
      )}
    </section>
  )
}

function Steps({ steps }: { steps: OperationStep[] }) {
  return (
    <ItemGroup aria-label="Preparation steps">
      {steps.map((step) => (
        <Item key={step.name} render={<li />} aria-label={step.name} variant="outline" size="sm">
          <ItemMedia variant="icon">{STATES[step.state].icon}</ItemMedia>
          <ItemContent>
            <ItemTitle>{step.name}</ItemTitle>
            <ItemDescription>{step.detail}</ItemDescription>
          </ItemContent>
          <ItemActions>
            <Badge variant={step.state === "failed" ? "destructive" : "secondary"}>
              {STATES[step.state].label}
            </Badge>
          </ItemActions>
        </Item>
      ))}
    </ItemGroup>
  )
}

function Fields({ fields }: { fields: SetupField[] }) {
  return (
    <ItemGroup aria-label="Setup fields">
      {fields.map((field) => (
        <Item key={field.name} render={<li />} aria-label={field.label} variant="outline" size="sm">
          <ItemContent>
            <ItemTitle>{field.label}</ItemTitle>
            <ItemDescription>{field.description}</ItemDescription>
          </ItemContent>
          <ItemActions>
            <Badge variant="outline">{field.kind === "secret" ? "Secret" : "Configuration"}</Badge>
            {!field.required && <Badge variant="secondary">Optional</Badge>}
          </ItemActions>
        </Item>
      ))}
    </ItemGroup>
  )
}
