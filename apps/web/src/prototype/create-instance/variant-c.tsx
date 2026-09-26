// PROTOTYPE, throwaway. Variant C: create first, set up in place. The create form asks only for
// name, face, and package; `instance.create` carries no secrets. The instance page then holds a
// setup checklist: each secret through `instance.secrets.set`, each login through a setup
// container, and Start unlocks when every required item is done.
import { useState } from "react"

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
import { Progress } from "@/components/ui/progress"
import { cn } from "cn"
import { CheckIcon, CircleAlertIcon, CircleIcon, PlayIcon } from "lucide-react"

import { createCommand, type Flow, slug } from "./flow"
import { AvatarPicker, FirstChat, InstanceAvatar, LoginList, OperationSteps } from "./parts"
import { ALL, CREATE_STEPS, FAILURE, packageById, START_STEPS } from "./stub"

export const name = "Create first, set up in place"

export function VariantC({ flow }: { flow: Flow }) {
  const { state, patch, call, runOperation } = flow
  const card = packageById(state.packageId)
  const instanceId = slug(state.name)

  const create = () => {
    call("instance.create", createCommand({ ...state, values: {} }, false))
    call("operation.get", { operation_id: "op_create_1" })
    patch({ stage: "preparing" })
    // Nothing to validate yet: the answers arrive after creation.
    runOperation(
      CREATE_STEPS.filter((s) => s !== "validate setup"),
      () => patch({ stage: "setup" }),
    )
  }

  const start = () => {
    call("instance.start", { instance_id: instanceId })
    patch({ stage: "starting" })
    runOperation(
      ["validate setup", ...START_STEPS],
      () => {
        call("thread.create", { title: null })
        patch({ stage: "chat" })
      },
      state.failNext ? "validate setup" : undefined,
    )
  }

  if (state.stage === "choosing" || state.stage === "details") {
    return (
      <div className="flex justify-center p-8">
        <Card className="w-full max-w-md">
          <CardHeader>
            <CardTitle>New instance</CardTitle>
            <CardDescription>Everything else is set up on its page.</CardDescription>
          </CardHeader>
          <CardContent>
            <div className="flex flex-col gap-5">
              <div className="flex items-center gap-4">
                <InstanceAvatar name={state.name} shape={state.shape} color={state.color} />
                <Field>
                  <FieldLabel htmlFor="name">Name</FieldLabel>
                  <Input
                    id="name"
                    value={state.name}
                    onChange={(e) => patch({ name: e.target.value })}
                  />
                </Field>
              </div>
              <AvatarPicker state={state} patch={patch} />
              <div className="flex flex-col gap-1">
                {ALL.map((option) => (
                  <label
                    key={option.id}
                    className={cn(
                      "flex cursor-pointer items-center gap-3 rounded-md border p-2 text-sm",
                      state.packageId === option.id && "border-ring bg-muted",
                      !option.available && "pointer-events-none opacity-50",
                    )}
                  >
                    <input
                      type="radio"
                      name="package"
                      className="sr-only"
                      checked={state.packageId === option.id}
                      onChange={() => patch({ packageId: option.id })}
                    />
                    <span>{option.icon}</span>
                    <span className="flex-1 font-medium">{option.displayName}</span>
                    <span className="text-xs text-muted-foreground">
                      {option.available
                        ? `${option.fields.length} fields, ${option.logins.length} sign-ins`
                        : "soon"}
                    </span>
                  </label>
                ))}
              </div>
            </div>
          </CardContent>
          <CardFooter>
            <Button className="w-full" disabled={!state.name} onClick={create}>
              Create {state.name}
            </Button>
          </CardFooter>
        </Card>
      </div>
    )
  }

  if (state.stage === "chat") return <FirstChat state={state} card={card} />

  return (
    <div className="mx-auto flex w-full max-w-2xl flex-col gap-6 p-6">
      <div className="flex items-center gap-4">
        <InstanceAvatar name={state.name} shape={state.shape} color={state.color} />
        <div className="flex-1">
          <h2 className="text-lg font-semibold">{state.name}</h2>
          <p className="text-sm text-muted-foreground">{card.displayName}</p>
        </div>
        <Badge variant={state.stage === "failed" ? "destructive" : "secondary"}>
          {state.stage === "preparing"
            ? "preparing"
            : state.stage === "starting"
              ? "starting"
              : "stopped"}
        </Badge>
      </div>

      {state.stage === "preparing" ? (
        <OperationSteps steps={state.steps} />
      ) : (
        <SetupChecklist flow={flow} onStart={start} instanceId={instanceId} />
      )}
    </div>
  )
}

function SetupChecklist({
  flow,
  onStart,
  instanceId,
}: {
  flow: Flow
  onStart: () => void
  instanceId: string
}) {
  const { state, patch, call } = flow
  const card = packageById(state.packageId)
  const [saved, setSaved] = useState<Record<string, boolean>>(() =>
    state.stage === "ready" || state.stage === "starting"
      ? Object.fromEntries(card.fields.map((f) => [f.name, true]))
      : Object.fromEntries(card.fields.filter((f) => f.default).map((f) => [f.name, true])),
  )
  const required = card.fields.filter((f) => f.required)
  const loginsDone = card.logins.filter((l) => state.logins[l.id] === "done").length
  const done = required.filter((f) => saved[f.name]).length + loginsDone
  const total = required.length + card.logins.length
  const complete = done === total

  const save = (fieldName: string) => {
    const field = card.fields.find((f) => f.name === fieldName)
    if (field?.kind === "secret")
      call("instance.secrets.set", {
        instance_id: instanceId,
        secrets: { [fieldName]: "<redacted>" },
      })
    else
      call(
        "instance.config.set",
        { instance_id: instanceId, values: { [fieldName]: state.values[fieldName] } },
        true,
      )
    setSaved({ ...saved, [fieldName]: true })
    if (state.stage === "failed" && fieldName === FAILURE.field)
      patch({ stage: "setup", error: null })
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-col gap-2">
        <div className="flex justify-between text-sm">
          <span className="font-medium">Setup</span>
          <span className="text-muted-foreground">
            {done} of {total}
          </span>
        </div>
        <Progress value={(done / total) * 100} />
      </div>

      <ul className="flex flex-col gap-2">
        {card.fields.map((field) => {
          const failed = state.stage === "failed" && field.name === FAILURE.field
          const ok = saved[field.name] && !failed
          return (
            <li
              key={field.name}
              className={cn(
                "flex flex-col gap-2 rounded-lg border p-3",
                failed && "border-destructive",
              )}
            >
              <div className="flex items-center gap-2 text-sm">
                {failed ? (
                  <CircleAlertIcon className="size-4 text-destructive" />
                ) : ok ? (
                  <CheckIcon className="size-4" />
                ) : (
                  <CircleIcon className="size-4 text-muted-foreground" />
                )}
                <span className="flex-1 font-medium">{field.label}</span>
                {field.kind === "secret" && <Badge variant="outline">secret</Badge>}
              </div>
              {failed && <p className="text-sm text-destructive">{FAILURE.detail}</p>}
              {!ok && (
                <div className="flex gap-2">
                  <Input
                    type={field.kind === "secret" ? "password" : "text"}
                    placeholder={field.description}
                    value={state.values[field.name] ?? ""}
                    onChange={(e) =>
                      patch({ values: { ...state.values, [field.name]: e.target.value } })
                    }
                  />
                  <Button variant="outline" onClick={() => save(field.name)}>
                    Save
                  </Button>
                </div>
              )}
            </li>
          )
        })}
      </ul>

      {card.logins.length > 0 && <LoginList card={card} flow={flow} instanceId={instanceId} />}

      {state.stage === "starting" ? (
        <OperationSteps steps={state.steps} />
      ) : (
        <Button disabled={!complete} onClick={onStart} className="self-start">
          <PlayIcon /> Start {state.name}
        </Button>
      )}
    </div>
  )
}
