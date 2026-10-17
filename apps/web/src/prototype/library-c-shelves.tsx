// PROTOTYPE library C, "Shelves": the library grouped by where each entry came from, as a grid of
// compact cards that fills the width. A git source is one shelf with one check for all its skills.
// An entry opens to its own page: the file on the left, what is known about it in a column beside.
import { ArrowLeftIcon, GitBranchIcon, PlusIcon, RefreshCwIcon, Trash2Icon } from "lucide-react"
import { useState } from "react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Separator } from "@/components/ui/separator"
import { Textarea } from "@/components/ui/textarea"

import { libraryEntries, skillBody, type ProtoLibraryEntry } from "./fixtures"
import { HubFrame } from "./parts"

export const name = "Shelves"

const SHELVES: {
  title: string
  note: string
  has: (entry: ProtoLibraryEntry) => boolean
  check?: boolean
}[] = [
  {
    title: "Written by you",
    note: "Yours to edit. Nothing upstream to move to.",
    has: (e) => e.source === "written",
  },
  {
    title: "Shipped with kinby",
    note: "kinby updates them. Your edits stay until you move.",
    has: (e) => e.source === "shipped",
    check: true,
  },
  {
    title: "jorgesolerrr/skills",
    note: "From git at a41c9e2.",
    has: (e) => e.source === "git",
    check: true,
  },
]

export function LibraryC() {
  const [open, setOpen] = useState<ProtoLibraryEntry>()
  return (
    <HubFrame active="Library">
      {open === undefined ? (
        <div className="flex flex-col gap-8 px-8 py-6">
          <header className="flex flex-wrap items-end gap-3">
            <div className="flex flex-1 flex-col gap-1">
              <h1 className="font-heading text-2xl font-semibold">Library</h1>
              <p className="text-sm text-muted-foreground">
                {libraryEntries.filter((entry) => entry.kind === "skill").length} skills and{" "}
                {libraryEntries.filter((entry) => entry.kind === "factory").length} factories any
                instance can pick.
              </p>
            </div>
            <Button variant="outline">
              <GitBranchIcon data-icon="inline-start" />
              Add from git
            </Button>
            <Button>
              <PlusIcon data-icon="inline-start" />
              Write a skill
            </Button>
          </header>
          {SHELVES.map((shelf) => (
            <section key={shelf.title} aria-label={shelf.title} className="flex flex-col gap-3">
              <div className="flex items-baseline gap-3">
                <h2 className="font-heading text-lg font-semibold">{shelf.title}</h2>
                <p className="flex-1 text-sm text-muted-foreground">{shelf.note}</p>
                {shelf.check && (
                  <Button variant="ghost" size="sm">
                    <RefreshCwIcon data-icon="inline-start" />
                    Check for newer
                  </Button>
                )}
              </div>
              <div className="grid grid-cols-[repeat(auto-fill,minmax(16rem,1fr))] gap-3">
                {libraryEntries.filter(shelf.has).map((entry) => (
                  <button
                    key={entry.name}
                    type="button"
                    onClick={() => setOpen(entry)}
                    className="flex flex-col gap-2 rounded-lg border bg-card p-4 text-left outline-none hover:border-foreground/30 focus-visible:ring-3 focus-visible:ring-ring/50"
                  >
                    <span className="flex items-center gap-2">
                      <span className="min-w-0 flex-1 truncate font-mono text-sm font-semibold">
                        {entry.name}
                      </span>
                      {entry.kind === "factory" && <Badge variant="secondary">Factory</Badge>}
                    </span>
                    <span className="line-clamp-2 text-sm text-muted-foreground">
                      {entry.description}
                    </span>
                    {(entry.newer || entry.edited) && (
                      <span className="mt-auto flex gap-1 pt-1">
                        {entry.newer && <Badge variant="attention">Newer version</Badge>}
                        {entry.edited && <Badge variant="outline">Edited</Badge>}
                      </span>
                    )}
                  </button>
                ))}
              </div>
            </section>
          ))}
        </div>
      ) : (
        <div className="flex h-full flex-col">
          <header className="flex items-center gap-3 border-b px-6 py-3">
            <Button
              variant="ghost"
              size="icon-sm"
              aria-label="Back to the library"
              onClick={() => setOpen(undefined)}
            >
              <ArrowLeftIcon />
            </Button>
            <h1 className="flex-1 font-mono text-lg font-semibold">{open.name}</h1>
            <Button>Save</Button>
          </header>
          <div className="flex min-h-0 flex-1">
            <div className="flex min-w-0 flex-1 flex-col p-6">
              <Textarea
                aria-label="SKILL.md"
                className="min-h-0 flex-1 resize-none font-mono text-sm leading-relaxed"
                defaultValue={skillBody.replaceAll("morning-brief", open.name)}
              />
            </div>
            <aside className="flex w-80 shrink-0 flex-col gap-5 border-l p-6 text-sm">
              <p className="text-muted-foreground">{open.description}</p>
              <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-2">
                <dt className="text-muted-foreground">Kind</dt>
                <dd className="capitalize">{open.kind}</dd>
                <dt className="text-muted-foreground">Source</dt>
                <dd className="capitalize">{open.source}</dd>
                {open.git && (
                  <>
                    <dt className="text-muted-foreground">Repository</dt>
                    <dd className="truncate font-mono text-xs leading-5">{open.git.url}</dd>
                    <dt className="text-muted-foreground">Commit</dt>
                    <dd className="font-mono text-xs leading-5">{open.git.commit}</dd>
                  </>
                )}
                <dt className="text-muted-foreground">Files</dt>
                <dd className="font-mono text-xs leading-5">
                  {["SKILL.md", ...open.files].join(", ")}
                </dd>
              </dl>
              {open.source !== "written" && (
                <>
                  <Separator />
                  <div className="flex flex-col gap-2">
                    <h2 className="font-medium">Newer version</h2>
                    <p className="text-muted-foreground">
                      {open.newer
                        ? "One is out. Read the diff before you move to it."
                        : "Not checked since you opened it."}
                    </p>
                    <Button variant="outline" size="sm" className="self-start">
                      {open.newer ? "Read the diff" : "Check now"}
                    </Button>
                  </div>
                </>
              )}
              <Button variant="ghost" size="sm" className="mt-auto self-start text-destructive">
                <Trash2Icon data-icon="inline-start" />
                Delete {open.name}
              </Button>
            </aside>
          </div>
        </div>
      )}
    </HubFrame>
  )
}
