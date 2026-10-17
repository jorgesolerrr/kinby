// PROTOTYPE library B, "Table and drawer": every entry is a row in one full-width table, skills and
// factories together, so the page reads like an inventory: what it is, where it came from, whether
// it moved on. A row opens a wide drawer with the file, and the table stays behind it.
import { ChevronRightIcon, GitBranchIcon, PlusIcon, SearchIcon, Trash2Icon } from "lucide-react"
import { useState } from "react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { InputGroup, InputGroupAddon, InputGroupInput } from "@/components/ui/input-group"
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { Textarea } from "@/components/ui/textarea"
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group"

import { libraryEntries, skillBody, type ProtoLibraryEntry } from "./fixtures"
import { HubFrame } from "./parts"

export const name = "Table and drawer"

const SOURCES = { shipped: "Shipped", written: "Written", git: "Git" }
type Show = "all" | "skill" | "factory" | "newer"

export function LibraryB() {
  const [query, setQuery] = useState("")
  const [show, setShow] = useState<Show>("all")
  const [open, setOpen] = useState<ProtoLibraryEntry>()
  const q = query.trim().toLowerCase()
  const entries = libraryEntries.filter(
    (entry) =>
      (q === "" || `${entry.name} ${entry.description}`.includes(q)) &&
      (show === "all" || (show === "newer" ? entry.newer : entry.kind === show)),
  )

  return (
    <HubFrame active="Library">
      <div className="flex flex-col gap-4 px-8 py-6">
        <header className="flex flex-wrap items-end gap-4">
          <div className="flex flex-1 flex-col gap-1">
            <h1 className="font-heading text-2xl font-semibold">Library</h1>
            <p className="text-sm text-muted-foreground">
              The skills and factories an instance can pick. Each instance keeps its own copy.
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
        <div className="flex flex-wrap items-center gap-3">
          <InputGroup className="max-w-xs">
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
          <ToggleGroup
            value={[show]}
            onValueChange={(value) => setShow((value[0] as Show | undefined) ?? "all")}
            variant="outline"
            size="sm"
            aria-label="Show"
          >
            <ToggleGroupItem value="all">All</ToggleGroupItem>
            <ToggleGroupItem value="skill">Skills</ToggleGroupItem>
            <ToggleGroupItem value="factory">Factories</ToggleGroupItem>
            <ToggleGroupItem value="newer">Newer version</ToggleGroupItem>
          </ToggleGroup>
          <span className="ml-auto font-mono text-xs text-muted-foreground">
            {entries.length} of {libraryEntries.length}
          </span>
        </div>
        <div className="rounded-lg border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-56 pl-4">Name</TableHead>
                <TableHead className="w-24">Kind</TableHead>
                <TableHead>What it does</TableHead>
                <TableHead className="w-64">Source</TableHead>
                <TableHead className="w-36">State</TableHead>
                <TableHead className="w-10" />
              </TableRow>
            </TableHeader>
            <TableBody>
              {entries.map((entry) => (
                <TableRow
                  key={entry.name}
                  className="cursor-pointer"
                  onClick={() => setOpen(entry)}
                >
                  <TableCell className="pl-4 font-mono font-medium">{entry.name}</TableCell>
                  <TableCell className="text-muted-foreground capitalize">{entry.kind}</TableCell>
                  <TableCell className="max-w-0 truncate text-muted-foreground">
                    {entry.description}
                  </TableCell>
                  <TableCell className="text-muted-foreground">
                    {entry.git ? (
                      <span className="font-mono text-xs">
                        {entry.git.url.replace("github.com/", "")} @{entry.git.commit}
                      </span>
                    ) : (
                      SOURCES[entry.source]
                    )}
                  </TableCell>
                  <TableCell>
                    <span className="flex gap-1">
                      {entry.newer && <Badge variant="attention">Newer</Badge>}
                      {entry.edited && <Badge variant="outline">Edited</Badge>}
                    </span>
                  </TableCell>
                  <TableCell>
                    <ChevronRightIcon className="size-4 text-muted-foreground" />
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      </div>
      <Sheet open={open !== undefined} onOpenChange={(opened) => !opened && setOpen(undefined)}>
        <SheetContent className="w-full data-[side=right]:sm:max-w-2xl">
          {open !== undefined && (
            <>
              <SheetHeader>
                <SheetTitle className="flex items-center gap-2 font-mono">
                  {open.name}
                  <Badge variant="secondary">{SOURCES[open.source]}</Badge>
                </SheetTitle>
                <SheetDescription>{open.description}</SheetDescription>
              </SheetHeader>
              <div className="flex min-h-0 flex-1 flex-col gap-2 px-4">
                <Textarea
                  aria-label={open.kind === "skill" ? "SKILL.md" : "factory.yaml"}
                  className="min-h-0 flex-1 resize-none font-mono text-sm leading-relaxed"
                  defaultValue={skillBody.replaceAll("morning-brief", open.name)}
                />
                {open.files.length > 0 && (
                  <p className="text-xs text-muted-foreground">
                    Other files: {open.files.join(", ")}
                  </p>
                )}
              </div>
              <SheetFooter className="flex-row">
                {open.source !== "written" && (
                  <Button variant="outline">Check for a newer version</Button>
                )}
                <Button variant="ghost" className="ml-auto text-destructive">
                  <Trash2Icon data-icon="inline-start" />
                  Delete
                </Button>
                <Button>Save</Button>
              </SheetFooter>
            </>
          )}
        </SheetContent>
      </Sheet>
    </HubFrame>
  )
}
