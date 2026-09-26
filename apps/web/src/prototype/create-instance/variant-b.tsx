// PROTOTYPE, throwaway. Variant B: a wizard. The image is prepared right after the package is
// picked, so the form asks exactly what the built image's descriptor declares, then
// `instance.create` names the prepared image.
import { useState } from "react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Field, FieldLabel } from "@/components/ui/field"
import { Input } from "@/components/ui/input"
import { cn } from "cn"
import { CheckIcon, PlayIcon } from "lucide-react"

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
import { ALL, INITIALIZE_STEPS, packageById, PREPARE_IMAGE_STEPS, START_STEPS } from "./stub"

export const name = "Wizard, image first"

const RAIL = ["Package", "Identity", "Setup", "Sign in", "Start"] as const

export function VariantB({ flow }: { flow: Flow }) {
  const { state, patch, call, runOperation } = flow
  const card = packageById(state.packageId)
  const instanceId = slug(state.name)
  // Whether the package's image is built and its descriptor read; only this variant has the split.
  const [imageReady, setImageReady] = useState(false)

  const railIndex = {
    choosing: imageReady ? 1 : 0,
    details: 2,
    preparing: imageReady ? 2 : 0,
    failed: 2,
    setup: 3,
    ready: 4,
    starting: 4,
    chat: 5,
  }[state.stage]

  const pickPackage = (id: string) => {
    const picked = packageById(id)
    patch({ packageId: id, stage: "preparing" })
    setImageReady(false)
    call(
      "package.prepare",
      { id, distribution: picked.distribution, version: picked.version },
      true,
    )
    call("operation.get", { operation_id: "op_prepare_1" })
    runOperation(PREPARE_IMAGE_STEPS, () => {
      call("package.describe", { image_id: "sha256:9f2c…" }, true)
      setImageReady(true)
      patch({ stage: "choosing" })
    })
  }

  const create = () => {
    call("instance.create", { ...createCommand(state, true), revision: "sha256:9f2c…" })
    call("operation.get", { operation_id: "op_create_1" })
    patch({ stage: "preparing" })
    runOperation(
      INITIALIZE_STEPS,
      () => patch({ stage: card.logins.length > 0 ? "setup" : "ready" }),
      state.failNext ? "validate setup" : undefined,
    )
  }

  const start = () => {
    call("instance.start", { instance_id: instanceId })
    patch({ stage: "starting" })
    runOperation(START_STEPS, () => {
      call("thread.create", { title: null })
      patch({ stage: "chat" })
    })
  }

  if (state.stage === "chat") return <FirstChat state={state} card={card} />

  return (
    <div className="flex h-full">
      <ol className="flex w-48 shrink-0 flex-col gap-1 border-r p-4 text-sm">
        {RAIL.map((label, i) => (
          <li
            key={label}
            className={cn(
              "flex items-center gap-2 rounded-md px-2 py-1.5",
              i === railIndex && "bg-muted font-medium",
              i > railIndex && "text-muted-foreground",
            )}
          >
            <span className="flex size-5 items-center justify-center rounded-full border text-xs">
              {i < railIndex ? <CheckIcon className="size-3" /> : i + 1}
            </span>
            {label}
          </li>
        ))}
      </ol>

      <div className="flex flex-1 justify-center overflow-auto p-8">
        <div className="flex w-full max-w-lg flex-col gap-6">
          {railIndex === 0 && state.stage === "choosing" && (
            <>
              <h2 className="text-lg font-semibold">What should it start from?</h2>
              <ul className="flex flex-col divide-y rounded-lg border">
                {ALL.map((option) => (
                  <li key={option.id}>
                    <button
                      type="button"
                      disabled={!option.available}
                      onClick={() => pickPackage(option.id)}
                      className="flex w-full items-center gap-3 p-4 text-left hover:bg-muted disabled:opacity-50"
                    >
                      <span className="text-xl">{option.icon}</span>
                      <span className="flex flex-1 flex-col">
                        <span className="font-medium">{option.displayName}</span>
                        <span className="text-sm text-muted-foreground">{option.description}</span>
                      </span>
                      {option.distribution && <Badge variant="outline">{option.version}</Badge>}
                      {!option.available && <Badge variant="outline">Soon</Badge>}
                    </button>
                  </li>
                ))}
              </ul>
            </>
          )}

          {state.stage === "preparing" && (
            <>
              <h2 className="text-lg font-semibold">
                {imageReady ? `Creating ${state.name}` : `Preparing ${card.displayName}`}
              </h2>
              <p className="text-sm text-muted-foreground">
                {imageReady
                  ? "Checking your answers against the package and seeding the directory."
                  : "Building the image so the form below asks exactly what this version needs."}
              </p>
              <OperationSteps steps={state.steps} />
            </>
          )}

          {railIndex === 1 && (
            <>
              <h2 className="text-lg font-semibold">Give it a face</h2>
              <div className="flex items-center gap-6">
                <InstanceAvatar
                  name={state.name}
                  shape={state.shape}
                  color={state.color}
                  size="size-20 text-3xl"
                />
                <AvatarPicker state={state} patch={patch} />
              </div>
              <Field>
                <FieldLabel htmlFor="name">Name</FieldLabel>
                <Input
                  id="name"
                  value={state.name}
                  onChange={(e) => patch({ name: e.target.value })}
                />
              </Field>
              <Nav onBack={() => setImageReady(false)} onNext={() => patch({ stage: "details" })} />
            </>
          )}

          {(state.stage === "details" || state.stage === "failed") && (
            <>
              <h2 className="text-lg font-semibold">Set up {card.displayName}</h2>
              <p className="text-sm text-muted-foreground">
                Read from {card.distribution ?? "kinby's defaults"} {card.version} after the image
                was built.
              </p>
              <FailureAlert state={state} onFix={() => patch({ stage: "details", error: null })} />
              <FieldsForm
                fields={card.fields}
                state={state}
                patch={patch}
                errorField={state.error?.field}
              />
              {card.id === "vanilla" && <BehaviorPrompt state={state} patch={patch} />}
              <Nav onBack={() => patch({ stage: "choosing" })} onNext={create} next="Create" />
            </>
          )}

          {state.stage === "setup" && (
            <>
              <h2 className="text-lg font-semibold">Sign in to the subscriptions</h2>
              <p className="text-sm text-muted-foreground">
                {state.name} exists and is stopped. You can close this and finish later from its
                page.
              </p>
              <LoginList card={card} flow={flow} instanceId={instanceId} />
            </>
          )}

          {(state.stage === "ready" || state.stage === "starting") && (
            <>
              <div className="flex items-center gap-4">
                <InstanceAvatar name={state.name} shape={state.shape} color={state.color} />
                <div>
                  <h2 className="text-lg font-semibold">{state.name} is ready</h2>
                  <p className="text-sm text-muted-foreground">
                    Stopped. Starting it turns on its routines.
                  </p>
                </div>
              </div>
              {state.stage === "starting" ? (
                <OperationSteps steps={state.steps} />
              ) : (
                <div className="flex gap-2">
                  <Button onClick={start}>
                    <PlayIcon /> Start and chat
                  </Button>
                  <Button variant="outline">Leave it stopped</Button>
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  )
}

function Nav({
  onBack,
  onNext,
  next = "Next",
}: {
  onBack: () => void
  onNext: () => void
  next?: string
}) {
  return (
    <div className="flex justify-between">
      <Button variant="ghost" onClick={onBack}>
        Back
      </Button>
      <Button onClick={onNext}>{next}</Button>
    </div>
  )
}
