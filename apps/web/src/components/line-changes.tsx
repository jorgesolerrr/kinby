import type { Client, InstanceSummary, Line } from "@kinby/contract"
import { CircleXIcon, EllipsisIcon, TrashIcon } from "lucide-react"
import { type FormEvent, type ReactNode, useId, useState } from "react"

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { Field, FieldDescription, FieldLabel } from "@/components/ui/field"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Spinner } from "@/components/ui/spinner"
import { instanceName } from "@/lib/instances"
import { reason } from "@/lib/operation"
import { FACTORIES_PATH, navigate } from "@/lib/selection"

type Caller = Pick<Client, "call">

type Change = "owner" | "model" | "budget" | "remove"

/**
 * The line's rare changes, behind its menu: its owner, its model, its daily budget, and removing
 * it. A change the hub takes reads the line again through `onChanged`, and a removed line goes
 * back to the Factories area. `name` is the line's, and `workbench` its workbench's.
 */
export function LineMenu({
  caller,
  line,
  name,
  workbench,
  instances,
  onChanged,
}: {
  caller: Caller
  line: Line
  name: string
  workbench: string
  instances: InstanceSummary[]
  onChanged: () => void
}) {
  const [change, setChange] = useState<Change>()
  const close = () => setChange(undefined)
  const changed = () => {
    close()
    onChanged()
  }
  const update = (fields: Pick<Line, "model" | "usd_per_day">) =>
    caller.call("line.update", { line_id: line.line_id, ...fields })
  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger
          render={<Button variant="ghost" size="icon" aria-label="More actions" />}
        >
          <EllipsisIcon />
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end">
          <DropdownMenuItem onClick={() => setChange("owner")}>Change owner…</DropdownMenuItem>
          <DropdownMenuItem onClick={() => setChange("model")}>Change model…</DropdownMenuItem>
          <DropdownMenuItem onClick={() => setChange("budget")}>
            Change daily budget…
          </DropdownMenuItem>
          <DropdownMenuSeparator />
          <DropdownMenuItem variant="destructive" onClick={() => setChange("remove")}>
            <TrashIcon />
            Remove line
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
      <Dialog open={change === "owner"} onOpenChange={(open) => !open && close()}>
        <DialogContent>
          <OwnerForm
            title={`Change the owner of ${name}`}
            line={line}
            workbench={workbench}
            instances={instances}
            onMove={(owner_id) =>
              caller.call("line.owner.set", { line_id: line.line_id, owner_id })
            }
            onDone={changed}
          />
        </DialogContent>
      </Dialog>
      <Dialog open={change === "model"} onOpenChange={(open) => !open && close()}>
        <DialogContent>
          <TextForm
            title={`Change the model of ${name}`}
            lead="An agent step that names no model of its own runs this one. Empty is none."
            label="Model"
            initial={line.model ?? ""}
            onSave={(text) =>
              update({ model: text === "" ? null : text, usd_per_day: line.usd_per_day })
            }
            onDone={changed}
          />
        </DialogContent>
      </Dialog>
      <Dialog open={change === "budget"} onOpenChange={(open) => !open && close()}>
        <DialogContent>
          <TextForm
            title={`Change the daily budget of ${name}`}
            lead="Once what the line's runs cost on the API in a UTC day reaches it, the next agent step fails before it starts. Empty is none."
            label="Daily budget in USD"
            type="number"
            initial={line.usd_per_day === null ? "" : String(line.usd_per_day)}
            onSave={(text) =>
              update({ model: line.model, usd_per_day: text === "" ? null : Number(text) })
            }
            onDone={changed}
          />
        </DialogContent>
      </Dialog>
      <AlertDialog open={change === "remove"} onOpenChange={(open) => !open && close()}>
        <AlertDialogContent>
          <RemoveForm
            name={name}
            workbench={workbench}
            onRemove={() => caller.call("line.remove", { line_id: line.line_id })}
          />
        </AlertDialogContent>
      </AlertDialog>
    </>
  )
}

/** Run `save`, then `onDone`, holding back a second submission and keeping why the hub refused. */
function useSaving(onDone: () => void) {
  const [saving, setSaving] = useState(false)
  const [failure, setFailure] = useState<string>()
  const save = async (call: () => Promise<unknown>) => {
    setSaving(true)
    setFailure(undefined)
    try {
      await call()
      onDone()
    } catch (error) {
      setFailure(reason(error))
      setSaving(false)
    }
  }
  return { saving, failure, save }
}

function OwnerForm({
  title,
  line,
  workbench,
  instances,
  onMove,
  onDone,
}: {
  title: string
  line: Line
  workbench: string
  instances: InstanceSummary[]
  onMove: (owner: string) => Promise<unknown>
  onDone: () => void
}) {
  const id = useId()
  const [owner, setOwner] = useState(line.owner_id)
  const { saving, failure, save } = useSaving(onDone)
  const items = instances.map((instance) => ({
    value: instance.instance_id,
    label: instanceName(instance),
  }))
  return (
    <ChangeForm
      title={title}
      lead={`Its runs, its intake routine and the memory marked ${workbench} move to the new owner. Refused while a run of the line is running a step.`}
      submit="Move"
      saving={saving}
      failure={failure}
      unchanged={owner === line.owner_id}
      onSubmit={() => save(() => onMove(owner))}
    >
      <Field>
        <FieldLabel htmlFor={id}>New owner</FieldLabel>
        <Select
          items={items}
          value={owner}
          onValueChange={(picked) => typeof picked === "string" && setOwner(picked)}
        >
          <SelectTrigger id={id} aria-label="New owner">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectGroup>
              {items.map((item) => (
                <SelectItem key={item.value} value={item.value}>
                  {item.label}
                </SelectItem>
              ))}
            </SelectGroup>
          </SelectContent>
        </Select>
      </Field>
    </ChangeForm>
  )
}

function TextForm({
  title,
  lead,
  label,
  type = "text",
  initial,
  onSave,
  onDone,
}: {
  title: string
  lead: string
  label: string
  type?: "text" | "number"
  initial: string
  onSave: (text: string) => Promise<unknown>
  onDone: () => void
}) {
  const id = useId()
  const [text, setText] = useState(initial)
  const { saving, failure, save } = useSaving(onDone)
  return (
    <ChangeForm
      title={title}
      submit="Save"
      saving={saving}
      failure={failure}
      unchanged={text.trim() === initial}
      onSubmit={() => save(() => onSave(text.trim()))}
    >
      <Field>
        <FieldLabel htmlFor={id}>{label}</FieldLabel>
        <Input
          id={id}
          type={type}
          min={type === "number" ? 0 : undefined}
          step="any"
          autoComplete="off"
          value={text}
          onChange={(event) => setText(event.target.value)}
        />
        <FieldDescription>{lead}</FieldDescription>
      </Field>
    </ChangeForm>
  )
}

/** A change's dialog body: its form, why the hub refused it, and its submit. */
function ChangeForm({
  title,
  lead,
  submit,
  saving,
  failure,
  unchanged,
  onSubmit,
  children,
}: {
  title: string
  lead?: string
  submit: string
  saving: boolean
  failure: string | undefined
  unchanged: boolean
  onSubmit: () => void
  children: ReactNode
}) {
  return (
    <form
      className="flex flex-col gap-6"
      onSubmit={(event: FormEvent) => {
        event.preventDefault()
        onSubmit()
      }}
    >
      <DialogHeader>
        <DialogTitle>{title}</DialogTitle>
        {lead !== undefined && <DialogDescription>{lead}</DialogDescription>}
      </DialogHeader>
      {children}
      {failure !== undefined && <Refused failure={failure} />}
      <DialogFooter>
        <Button type="submit" disabled={saving || unchanged}>
          {saving && <Spinner data-icon="inline-start" />}
          {submit}
        </Button>
      </DialogFooter>
    </form>
  )
}

function RemoveForm({
  name,
  workbench,
  onRemove,
}: {
  name: string
  workbench: string
  onRemove: () => Promise<unknown>
}) {
  const { saving, failure, save } = useSaving(() => navigate(FACTORIES_PATH))
  return (
    <>
      <AlertDialogHeader>
        <AlertDialogTitle>Remove {name}?</AlertDialogTitle>
        <AlertDialogDescription>
          Its intake routine goes with it. The workbench {workbench} stays as it is.
        </AlertDialogDescription>
      </AlertDialogHeader>
      {failure !== undefined && <Refused failure={failure} />}
      <AlertDialogFooter>
        <AlertDialogCancel disabled={saving}>Cancel</AlertDialogCancel>
        <Button variant="destructive" disabled={saving} onClick={() => void save(onRemove)}>
          {saving && <Spinner data-icon="inline-start" />}
          Remove
        </Button>
      </AlertDialogFooter>
    </>
  )
}

function Refused({ failure }: { failure: string }) {
  return (
    <Alert variant="destructive">
      <CircleXIcon />
      <AlertTitle>The hub refused it</AlertTitle>
      <AlertDescription>{failure}</AlertDescription>
    </Alert>
  )
}
