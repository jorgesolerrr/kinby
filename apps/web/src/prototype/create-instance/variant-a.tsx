// PROTOTYPE, throwaway. Variant A: one screen, everything up front, one `instance.create`.
// The card list carries a copy of each package's declared fields; the hub checks it against
// the real descriptor during "inspect descriptor".
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Field, FieldLabel } from "@/components/ui/field"
import { Input } from "@/components/ui/input"
import { Separator } from "@/components/ui/separator"
import { cn } from "cn"
import { PlayIcon } from "lucide-react"

import { createCommand, type Flow, slug } from "./flow"
import {
  AvatarPicker,
  BehaviorPrompt,
  FailureAlert,
  FieldsForm,
  FirstChat,
  InstanceAvatar,
  LoginList,
  OperationSteps,
} from "./parts"
import { ALL, CREATE_STEPS, packageById, START_STEPS } from "./stub"

export const name = "One screen"

export function VariantA({ flow }: { flow: Flow }) {
  const { state, patch, call, runOperation } = flow
  const card = packageById(state.packageId)
  const instanceId = slug(state.name)

  const prepare = () => {
    call("instance.create", createCommand(state, true))
    call("operation.get", { operation_id: "op_create_1" })
    patch({ stage: "preparing" })
    runOperation(
      CREATE_STEPS,
      () => patch({ stage: card.logins.length > 0 ? "setup" : "ready" }),
      state.failNext ? "validate setup" : undefined,
    )
  }

  const start = () => {
    call("instance.start", { instance_id: instanceId })
    patch({ stage: "starting" })
    runOperation(START_STEPS, () => {
      call("thread.create", { title: null }, false)
      patch({ stage: "chat" })
    })
  }

  if (state.stage === "choosing" || state.stage === "details") {
    return (
      <div className="mx-auto flex w-full max-w-3xl flex-col gap-8 p-6">
        <div className="flex flex-col items-center gap-4">
          <InstanceAvatar
            name={state.name}
            shape={state.shape}
            color={state.color}
            size="size-24 text-4xl"
          />
          <AvatarPicker state={state} patch={patch} />
          <Field className="w-72">
            <FieldLabel htmlFor="name" className="sr-only">
              Name
            </FieldLabel>
            <Input
              id="name"
              className="text-center"
              placeholder="Name your kinby"
              value={state.name}
              onChange={(e) => patch({ name: e.target.value })}
            />
          </Field>
        </div>

        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          {ALL.map((option) => (
            <button
              key={option.id}
              type="button"
              disabled={!option.available}
              onClick={() => patch({ packageId: option.id, stage: "details" })}
              className="text-left disabled:opacity-50"
            >
              <div
                className={cn(
                  "h-full rounded-xl",
                  state.packageId === option.id && "ring-2 ring-ring",
                )}
              >
                <Card size="sm" className="h-full">
                  <CardHeader>
                    <span className="text-xl">{option.icon}</span>
                    <CardTitle>{option.displayName}</CardTitle>
                    <CardDescription>{option.description}</CardDescription>
                  </CardHeader>
                  {!option.available && (
                    <CardFooter>
                      <Badge variant="outline">Not published yet</Badge>
                    </CardFooter>
                  )}
                </Card>
              </div>
            </button>
          ))}
        </div>

        {state.stage === "details" && (
          <>
            <Separator />
            <FieldsForm fields={card.fields} state={state} patch={patch} />
            {card.id === "vanilla" && <BehaviorPrompt state={state} patch={patch} />}
            {card.logins.length > 0 && (
              <p className="text-sm text-muted-foreground">
                After preparation you sign in to {card.logins.map((l) => l.label).join(" and ")}.
                Nothing starts until you press Start.
              </p>
            )}
          </>
        )}
        <Button
          size="lg"
          className="self-center"
          disabled={state.stage !== "details"}
          onClick={prepare}
        >
          Prepare {state.name || "instance"}
        </Button>
      </div>
    )
  }

  return (
    <div className="flex h-full flex-col">
      <InstanceHeader flow={flow} />
      {state.stage === "chat" ? (
        <FirstChat state={state} card={card} />
      ) : (
        <div className="mx-auto flex w-full max-w-xl flex-col gap-6 p-6">
          {(state.stage === "preparing" || state.stage === "starting") && (
            <Card>
              <CardHeader>
                <CardTitle>{state.stage === "preparing" ? "Preparing" : "Starting"}</CardTitle>
                <CardDescription>
                  {state.stage === "preparing"
                    ? "Building the image and seeding the instance directory. You can leave; it keeps going."
                    : "Routines stay paused until the instance is healthy."}
                </CardDescription>
              </CardHeader>
              <CardContent>
                <OperationSteps steps={state.steps} />
              </CardContent>
            </Card>
          )}
          {state.stage === "failed" && (
            <>
              <OperationSteps steps={state.steps} />
              <FailureAlert state={state} onFix={() => patch({ stage: "details" })} />
            </>
          )}
          {state.stage === "setup" && (
            <Card>
              <CardHeader>
                <CardTitle>Stopped: sign-in pending</CardTitle>
                <CardDescription>
                  Each sign-in runs in a temporary setup container. The scheduler does not start.
                </CardDescription>
              </CardHeader>
              <CardContent>
                <LoginList card={card} flow={flow} instanceId={instanceId} />
              </CardContent>
            </Card>
          )}
          {state.stage === "ready" && (
            <Card>
              <CardHeader>
                <CardTitle>Ready, stopped</CardTitle>
                <CardDescription>Setup passed. Starting it enables its routines.</CardDescription>
              </CardHeader>
              <CardFooter>
                <Button onClick={start}>
                  <PlayIcon /> Start {state.name}
                </Button>
              </CardFooter>
            </Card>
          )}
        </div>
      )}
    </div>
  )
}

const badge: Record<string, string> = {
  preparing: "preparing",
  failed: "failed",
  setup: "stopped · setup pending",
  ready: "stopped",
  starting: "starting",
  chat: "running",
}

function InstanceHeader({ flow }: { flow: Flow }) {
  const { state } = flow
  const card = packageById(state.packageId)
  return (
    <div className="flex items-center gap-3 border-b px-6 py-4">
      <InstanceAvatar
        name={state.name}
        shape={state.shape}
        color={state.color}
        size="size-10 text-lg"
      />
      <div className="flex flex-col">
        <span className="font-medium">{state.name}</span>
        <span className="text-xs text-muted-foreground">
          {card.distribution === null
            ? "Vanilla"
            : `${card.displayName} · ${card.distribution} ${card.version}`}
        </span>
      </div>
      <Badge variant={state.stage === "failed" ? "destructive" : "secondary"} className="ml-auto">
        {badge[state.stage]}
      </Badge>
    </div>
  )
}
