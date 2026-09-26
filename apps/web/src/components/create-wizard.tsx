import { browserClock } from "@kinby/contract"
import type { Client, Clock, OperationState, OperationStep, SetupField } from "@kinby/contract"
import { useEffect, useState } from "react"
import type * as React from "react"

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card"
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
import { CircleCheckIcon, CircleXIcon } from "lucide-react"

const STATES: Record<OperationState, { label: string; icon: React.ReactNode }> = {
  pending: { label: "Waiting", icon: <Spinner /> },
  running: { label: "Running", icon: <Spinner /> },
  succeeded: { label: "Done", icon: <CircleCheckIcon /> },
  failed: { label: "Failed", icon: <CircleXIcon /> },
}

/** Create an instance. Its package step picks what the instance starts from and prepares it. */
export function CreateWizard({
  caller,
  clock = browserClock,
}: {
  caller: Pick<Client, "call">
  clock?: Clock
}) {
  const [pick, setPick] = useState(0)
  const preparation = usePreparation(caller, pick, clock)

  return (
    <div className="flex flex-col gap-6 p-6">
      <header className="flex flex-col gap-1">
        <h1 className="text-lg font-medium">New instance</h1>
        <p className="text-sm text-muted-foreground">
          Package: pick what the instance starts from.
        </p>
      </header>
      <Card className="max-w-sm">
        <CardHeader>
          <CardTitle>Vanilla</CardTitle>
          <CardDescription>kinby's built-in defaults, with no package.</CardDescription>
        </CardHeader>
        <CardFooter>
          <Button
            disabled={preparation?.state === "preparing" || preparation?.state === "prepared"}
            onClick={() => setPick((current) => current + 1)}
          >
            Prepare vanilla
          </Button>
        </CardFooter>
      </Card>
      {preparation !== undefined && <PreparationView preparation={preparation} />}
    </div>
  )
}

/** The preparation of the picked card: nothing before a pick, then each poll of that pick. */
function usePreparation(
  caller: Pick<Client, "call">,
  pick: number,
  clock: Clock,
): Preparation | undefined {
  const [followed, setFollowed] = useState<{ pick: number; preparation: Preparation }>()
  useEffect(() => {
    if (pick === 0) return
    return followPreparation(
      caller,
      null,
      (preparation) => {
        setFollowed({ pick, preparation })
      },
      clock,
    )
  }, [caller, pick, clock])
  if (pick === 0) return undefined
  // The previous pick's report stays stored until this one answers.
  if (followed?.pick !== pick) return { state: "preparing", steps: [] }
  return followed.preparation
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
