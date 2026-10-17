// PROTOTYPE library A, "List and editor": a mail-client split. The entries stay on the left with a
// search and a Skills / Factories switch, and the open one fills the rest of the window: a toolbar
// with its source and actions, then SKILL.md at full height. Nothing sits below the list any more.
import { CheckIcon, GitBranchIcon, PlusIcon, SearchIcon, Trash2Icon } from "lucide-react"
import { useState } from "react"

import { Alert, AlertAction, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { InputGroup, InputGroupAddon, InputGroupInput } from "@/components/ui/input-group"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Textarea } from "@/components/ui/textarea"

import { libraryEntries, skillBody } from "./fixtures"
import { HubFrame } from "./parts"

export const name = "List and editor"

const SOURCES = { shipped: "Shipped", written: "Written", git: "Git" }

export function LibraryA() {
  const [kind, setKind] = useState<"skill" | "factory">("skill")
  const [query, setQuery] = useState("")
  const [openName, setOpenName] = useState("weekly-review")
  const q = query.trim().toLowerCase()
  const entries = libraryEntries.filter(
    (entry) =>
      entry.kind === kind && (q === "" || `${entry.name} ${entry.description}`.includes(q)),
  )
  const open = libraryEntries.find((entry) => entry.name === openName)

  return (
    <HubFrame active="Library">
      <div className="flex h-full">
        <section aria-label="Entries" className="flex w-96 shrink-0 flex-col border-r">
          <div className="flex flex-col gap-3 border-b p-4">
            <div className="flex items-center gap-2">
              <h1 className="flex-1 font-heading text-xl font-semibold">Library</h1>
              <Button size="sm" variant="outline">
                <GitBranchIcon data-icon="inline-start" />
                Add from git
              </Button>
              <Button size="sm">
                <PlusIcon data-icon="inline-start" />
                Write
              </Button>
            </div>
            <InputGroup>
              <InputGroupInput
                aria-label="Search the library"
                placeholder="Search the library"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
              />
              <InputGroupAddon>
                <SearchIcon />
              </InputGroupAddon>
            </InputGroup>
            <Tabs value={kind} onValueChange={(value) => setKind(value as "skill" | "factory")}>
              <TabsList className="w-full">
                <TabsTrigger value="skill">
                  Skills{" "}
                  <span className="font-mono text-xs text-muted-foreground">
                    {libraryEntries.filter((entry) => entry.kind === "skill").length}
                  </span>
                </TabsTrigger>
                <TabsTrigger value="factory">
                  Factories{" "}
                  <span className="font-mono text-xs text-muted-foreground">
                    {libraryEntries.filter((entry) => entry.kind === "factory").length}
                  </span>
                </TabsTrigger>
              </TabsList>
            </Tabs>
          </div>
          <ul className="flex min-h-0 flex-1 flex-col gap-0.5 overflow-y-auto p-2">
            {entries.map((entry) => (
              <li key={entry.name}>
                <button
                  type="button"
                  aria-current={entry.name === openName ? "page" : undefined}
                  onClick={() => setOpenName(entry.name)}
                  className="flex w-full flex-col gap-0.5 rounded-md px-3 py-2 text-left outline-none hover:bg-accent focus-visible:ring-3 focus-visible:ring-ring/50 aria-[current=page]:bg-accent"
                >
                  <span className="flex items-center gap-2">
                    <span className="min-w-0 flex-1 truncate font-mono text-sm font-medium">
                      {entry.name}
                    </span>
                    {entry.newer && (
                      <span aria-label="Newer version" className="size-2 rounded-full bg-ember" />
                    )}
                    <span className="text-xs text-muted-foreground">
                      {SOURCES[entry.source]}
                      {entry.edited && " · edited"}
                    </span>
                  </span>
                  <span className="truncate text-xs text-muted-foreground">
                    {entry.description}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </section>

        {open !== undefined && (
          <section aria-label={open.name} className="flex min-w-0 flex-1 flex-col">
            <header className="flex flex-wrap items-center gap-x-3 gap-y-2 border-b px-6 py-4">
              <div className="flex min-w-0 flex-1 flex-col gap-1">
                <h2 className="flex items-center gap-2 font-mono text-lg font-semibold">
                  {open.name}
                  <Badge variant="secondary">{SOURCES[open.source]}</Badge>
                  {open.edited && <Badge variant="outline">Edited</Badge>}
                </h2>
                <p className="truncate text-sm text-muted-foreground">
                  {open.git ? `${open.git.url} at ${open.git.commit}` : open.description}
                </p>
              </div>
              {open.source !== "written" && (
                <Button variant="outline" size="sm">
                  Check for a newer version
                </Button>
              )}
              <Button variant="ghost" size="sm" className="text-destructive">
                <Trash2Icon data-icon="inline-start" />
                Delete
              </Button>
              <Button size="sm">
                <CheckIcon data-icon="inline-start" />
                Save
              </Button>
            </header>
            {open.newer && (
              <Alert className="mx-6 mt-4 w-auto">
                <AlertTitle>A newer version is out</AlertTitle>
                <AlertDescription>
                  Read the diff, then move the library&apos;s copy to it. Instances that picked it
                  keep theirs.
                </AlertDescription>
                <AlertAction>
                  <Button size="sm" variant="outline">
                    Read the diff
                  </Button>
                </AlertAction>
              </Alert>
            )}
            <div className="flex min-h-0 flex-1 flex-col gap-2 p-6">
              <Textarea
                aria-label="SKILL.md"
                className="min-h-0 flex-1 resize-none font-mono text-sm leading-relaxed"
                defaultValue={skillBody.replaceAll("morning-brief", open.name)}
              />
              <p className="flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
                SKILL.md
                {open.files.map((file) => (
                  <Badge key={file} variant="outline" className="font-mono">
                    {file}
                  </Badge>
                ))}
              </p>
            </div>
          </section>
        )}
      </div>
    </HubFrame>
  )
}
