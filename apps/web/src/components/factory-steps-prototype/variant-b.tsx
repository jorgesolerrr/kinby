// PROTOTYPE (#1450), throwaway. Variant B, "Table and panel": the steps as a dense table whose
// Routes column spells each route out, with an on/off switch per row. The step editor is a side
// panel next to the table, its settings, text and read-only values on tabs; a text editor can pop
// out to fill the screen. A refused off highlights the blocking rows in the table.
import { cn } from "cn"
import { Maximize2Icon, PlusIcon, Trash2Icon, XIcon } from "lucide-react"
import { useState } from "react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { ResizableHandle, ResizablePanel, ResizablePanelGroup } from "@/components/ui/resizable"
import { Switch } from "@/components/ui/switch"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Textarea } from "@/components/ui/textarea"

import { FACTORY, type Step } from "./data"
import { go, useAt } from "./nav"
import {
  CodeEditor,
  FromRun,
  KindIcon,
  NewStepFields,
  ReadOnlyValues,
  Refusal,
  RunPage,
  Saved,
  Shell,
  StepFields,
} from "./shared"
import { isDirty, saveAndRetry, stepTexts, targetText, useNewStep } from "./logic"
import { addStep, blockers, deleteStep, saveStep, setOff, useFactory } from "./store"

export function VariantB() {
  const [kind] = useAt()
  return kind === "run" ? (
    <Shell>
      <RunPage editAt={(step) => `step:${step}`} />
    </Shell>
  ) : (
    <Shell fixed>
      <Split />
    </Shell>
  )
}

function Split() {
  const [kind, id] = useAt()
  const [refused, setRefused] = useState<string>()
  const open = kind === "step" || kind === "add"
  return (
    <ResizablePanelGroup orientation="horizontal" className="min-h-0 flex-1">
      <ResizablePanel id="table" defaultSize={open ? "48%" : "100%"} minSize="420px">
        <div className="flex h-full flex-col gap-4 overflow-y-auto px-4 py-6 md:px-6">
          <div className="flex items-end justify-between gap-4">
            <div>
              <p className="text-sm text-muted-foreground">Factories</p>
              <h1 className="font-mono text-2xl font-semibold">{FACTORY}</h1>
            </div>
          </div>
          <Saved />
          <StepTable
            selected={kind === "step" ? id : undefined}
            refused={refused}
            setRefused={setRefused}
          />
        </div>
      </ResizablePanel>
      {open && (
        <>
          <ResizableHandle withHandle />
          <ResizablePanel id={`panel-${kind}`} defaultSize="52%" minSize="380px">
            {kind === "step" ? (
              <Panel key={id} id={id} refused={refused === id} setRefused={setRefused} />
            ) : (
              <AddPanel after={id} />
            )}
          </ResizablePanel>
        </>
      )}
    </ResizablePanelGroup>
  )
}

function routeLines(step: Step): { text: string; failed?: boolean }[] {
  const lines: { text: string; failed?: boolean }[] = []
  if (step.failed) lines.push({ text: `failed → ${targetText(step.failed)}`, failed: true })
  for (const [outcome, target] of Object.entries(step.outcomes ?? {})) {
    lines.push({ text: `${outcome} → ${targetText(target)}` })
  }
  if (step.resume) lines.push({ text: `resumes ${step.resume}'s session` })
  return lines
}

function StepTable({
  selected,
  refused,
  setRefused,
}: {
  selected?: string
  refused?: string
  setRefused: (id: string | undefined) => void
}) {
  const { steps } = useFactory()
  const blocking = refused === undefined ? [] : blockers(steps, refused).map(({ step }) => step)
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead className="w-8">#</TableHead>
          <TableHead>Step</TableHead>
          <TableHead>Runs on</TableHead>
          <TableHead>Routes</TableHead>
          <TableHead className="w-12 text-right">On</TableHead>
          <TableHead className="w-8" />
        </TableRow>
      </TableHeader>
      <TableBody>
        {steps.map((step, index) => (
          <TableRow
            key={step.id}
            data-state={selected === step.id ? "selected" : undefined}
            onClick={() => go(`step:${step.id}`)}
            className="cursor-pointer align-top"
          >
            <TableCell>
              <span className="text-muted-foreground tabular-nums">{index + 1}</span>
            </TableCell>
            <TableCell>
              <span className={cn("flex items-center gap-2", step.off && "text-muted-foreground")}>
                <KindIcon kind={step.kind} />
                <span className={cn("font-mono font-medium", step.off && "line-through")}>
                  {step.id}
                </span>
                {step.off && <Badge variant="outline">Off</Badge>}
                {step.added && <Badge variant="success">New</Badge>}
                {blocking.includes(step.id) && <Badge variant="destructive">Blocks</Badge>}
                {refused === step.id && <Badge variant="destructive">Kept on</Badge>}
              </span>
            </TableCell>
            <TableCell>
              <span className="text-xs">
                {step.kind === "agent" ? (
                  <>
                    {step.engine}
                    <br />
                    <span className="text-muted-foreground">
                      {step.model} · {step.effort}
                    </span>
                  </>
                ) : (
                  <span className="text-muted-foreground">{step.kind}</span>
                )}
              </span>
            </TableCell>
            <TableCell className="whitespace-normal">
              <div className="text-xs">
                {routeLines(step).map(({ text, failed }) => (
                  <div key={text} className={cn(failed && "text-destructive")}>
                    {text}
                  </div>
                ))}
              </div>
            </TableCell>
            <TableCell className="text-right" onClick={(event) => event.stopPropagation()}>
              <Switch
                checked={!step.off}
                aria-label={`${step.id} on`}
                onCheckedChange={(on) => {
                  if (!on && blockers(steps, step.id).length > 0) {
                    setRefused(step.id)
                    go(`step:${step.id}`)
                    return
                  }
                  setRefused(undefined)
                  setOff(step.id, !on)
                }}
              />
            </TableCell>
            <TableCell onClick={(event) => event.stopPropagation()}>
              <Button
                size="icon-xs"
                variant="ghost"
                aria-label={`Add a step after ${step.id}`}
                onClick={() => go(`add:${step.id}`)}
              >
                <PlusIcon />
              </Button>
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  )
}

function Panel({
  id,
  refused,
  setRefused,
}: {
  id: string
  refused: boolean
  setRefused: (id: string | undefined) => void
}) {
  const { steps } = useFactory()
  const step = steps.find((each) => each.id === id)
  const [draft, setDraft] = useState(step)
  const [popped, setPopped] = useState<string>()
  if (step === undefined || draft === undefined) return null
  const texts = stepTexts(draft)
  const dirty = isDirty(draft, step)
  const poppedText = texts.find((text) => text.key === popped)
  return (
    <section aria-label={`${id} editor`} className="flex h-full flex-col border-l bg-background">
      <header className="flex flex-col gap-2 border-b p-4">
        <div className="flex items-center gap-2">
          <KindIcon kind={step.kind} />
          <h2 className="font-mono text-lg font-semibold">{step.id}</h2>
          <Badge variant="outline">{step.kind}</Badge>
          {step.off && <Badge variant="outline">Off</Badge>}
          <Button
            size="icon-sm"
            variant="ghost"
            className="ml-auto"
            aria-label="Close"
            onClick={() => {
              setRefused(undefined)
              go("factory")
            }}
          >
            <XIcon />
          </Button>
        </div>
        <Textarea
          aria-label="Description"
          rows={2}
          value={draft.description}
          onChange={(e) => setDraft({ ...draft, description: e.target.value })}
          className="resize-none"
        />
      </header>
      <div className="flex flex-col gap-3 px-4 pt-3 empty:hidden">
        <FromRun step={id} onSaveAndRetry={() => saveAndRetry(() => saveStep(id, draft))} />
        {refused && <Refusal id={id} onClose={() => setRefused(undefined)} />}
        {step.off && (
          <div className="flex items-center justify-between rounded-lg bg-muted px-3 py-2 text-sm">
            Off: runs skip it. Only an off step can be deleted.
            <Button
              size="sm"
              variant="ghost"
              onClick={() => {
                deleteStep(id)
                go("factory")
              }}
            >
              <Trash2Icon data-icon="inline-start" />
              Delete
            </Button>
          </div>
        )}
      </div>
      <div className="flex min-h-0 flex-1 flex-col px-4 pt-3">
        <Tabs defaultValue="settings" className="min-h-0 flex-1">
          <TabsList variant="line">
            <TabsTrigger value="settings">Settings</TabsTrigger>
            {texts.map((text) => (
              <TabsTrigger key={text.key} value={text.key}>
                {text.label.split(" · ")[0]}
              </TabsTrigger>
            ))}
            <TabsTrigger value="values">Values</TabsTrigger>
          </TabsList>
          <TabsContent value="settings" className="overflow-y-auto">
            <div className="py-3">
              <StepFields draft={draft} setDraft={setDraft} steps={steps} />
            </div>
          </TabsContent>
          {texts.map((text) => (
            <TabsContent key={text.key} value={text.key} className="flex min-h-0 flex-col">
              <div className="flex min-h-0 flex-1 flex-col gap-2 py-3">
                <div className="flex items-center justify-between text-xs text-muted-foreground">
                  {text.label}
                  <Button size="xs" variant="ghost" onClick={() => setPopped(text.key)}>
                    <Maximize2Icon data-icon="inline-start" />
                    Pop out
                  </Button>
                </div>
                <CodeEditor
                  className="flex-1"
                  file={text.file}
                  language={text.language}
                  value={text.value}
                  onChange={(value) => setDraft({ ...draft, ...text.put(value) })}
                />
              </div>
            </TabsContent>
          ))}
          <TabsContent value="values">
            <div className="py-3">
              <ReadOnlyValues step={step} />
            </div>
          </TabsContent>
        </Tabs>
      </div>
      <footer className="flex justify-end gap-2 border-t p-3">
        <Button variant="ghost" disabled={!dirty} onClick={() => setDraft(step)}>
          Discard
        </Button>
        <Button disabled={!dirty} onClick={() => saveStep(id, draft)}>
          Save
        </Button>
      </footer>
      <Dialog
        open={poppedText !== undefined}
        onOpenChange={(open) => !open && setPopped(undefined)}
      >
        <DialogContent className="sm:max-w-5xl">
          <DialogHeader>
            <DialogTitle>
              <span className="font-mono">{id}</span> · {poppedText?.label}
            </DialogTitle>
          </DialogHeader>
          {poppedText !== undefined && (
            <CodeEditor
              rows={36}
              file={poppedText.file}
              language={poppedText.language}
              value={poppedText.value}
              onChange={(value) => setDraft({ ...draft, ...poppedText.put(value) })}
            />
          )}
        </DialogContent>
      </Dialog>
    </section>
  )
}

function AddPanel({ after }: { after: string }) {
  const made = useNewStep()
  return (
    <section aria-label="Add a step" className="flex h-full flex-col border-l bg-background">
      <header className="flex items-center gap-2 border-b p-4">
        <h2 className="text-lg font-semibold">
          Add a step after <span className="font-mono">{after}</span>
        </h2>
        <Button
          size="icon-sm"
          variant="ghost"
          className="ml-auto"
          aria-label="Close"
          onClick={() => go("factory")}
        >
          <XIcon />
        </Button>
      </header>
      <div className="flex-1 overflow-y-auto p-4">
        <NewStepFields {...made.fields} />
      </div>
      <footer className="flex justify-end gap-2 border-t p-3">
        <Button
          disabled={!made.ready}
          onClick={() => {
            const step = made.step()
            addStep(after, step)
            go(`step:${step.id}`)
          }}
        >
          Add
        </Button>
      </footer>
    </section>
  )
}
