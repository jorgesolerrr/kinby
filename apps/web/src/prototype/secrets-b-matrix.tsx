// PROTOTYPE secrets B, "Who uses what": a matrix with a secret per row and an agent per column, so
// the page answers "what breaks if I replace this?" at a glance. A cell names the field the secret
// fills in that agent. Adding stays a dialog-sized form on top.
import { PlusIcon, Trash2Icon } from "lucide-react"

import { Button } from "@/components/ui/button"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"

import { instances, secrets } from "./fixtures"
import { AgentAvatar, HubFrame } from "./parts"
import { STATE } from "./sheet"

export const name = "Who uses what"

export function SecretsB() {
  return (
    <HubFrame active="Secrets">
      <div className="flex flex-col gap-4 px-8 py-6">
        <header className="flex flex-wrap items-end gap-4">
          <div className="flex flex-1 flex-col gap-1">
            <h1 className="font-heading text-2xl font-semibold">Secrets</h1>
            <p className="text-sm text-muted-foreground">
              {secrets.length} values the hub keeps, and the instances that read each one. The hub
              never shows a value back.
            </p>
          </div>
          <Button>
            <PlusIcon data-icon="inline-start" />
            Add a secret
          </Button>
        </header>
        <div className="rounded-lg border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="pl-4">Secret</TableHead>
                {instances.map((agent) => (
                  <TableHead key={agent.id} className="w-48">
                    <span className="flex items-center gap-2">
                      <AgentAvatar agent={agent} size="sm" />
                      <span className="flex flex-col leading-tight">
                        <span className="font-medium">{agent.name}</span>
                        <span className="text-xs font-normal text-muted-foreground">
                          {STATE[agent.state].label}
                        </span>
                      </span>
                    </span>
                  </TableHead>
                ))}
                <TableHead className="w-36" />
              </TableRow>
            </TableHeader>
            <TableBody>
              {secrets.map((secret) => (
                <TableRow key={secret.name}>
                  <TableCell className="pl-4 font-mono font-medium">{secret.name}</TableCell>
                  {instances.map((agent) => {
                    const link = secret.links.find((each) => each.agent === agent.name)
                    return (
                      <TableCell key={agent.id}>
                        {link ? (
                          <span className="flex items-center gap-1.5 font-mono text-xs">
                            <span
                              aria-hidden
                              className={`size-1.5 rounded-full ${link.running ? "bg-kin" : "bg-muted-foreground/40"}`}
                            />
                            {link.field}
                          </span>
                        ) : (
                          <span aria-label="Not linked" className="text-muted-foreground/50">
                            ·
                          </span>
                        )}
                      </TableCell>
                    )
                  })}
                  <TableCell className="pr-4 text-right">
                    <Button variant="outline" size="sm">
                      Replace
                    </Button>
                    <Tooltip>
                      <TooltipTrigger
                        render={
                          <Button
                            variant="ghost"
                            size="icon-sm"
                            aria-label={`Delete ${secret.name}`}
                          >
                            <Trash2Icon />
                          </Button>
                        }
                      />
                      <TooltipContent>Delete</TooltipContent>
                    </Tooltip>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
        <p className="flex items-center gap-4 text-xs text-muted-foreground">
          <span className="flex items-center gap-1.5">
            <span className="size-1.5 rounded-full bg-kin" /> instance running, takes a new value on
            recreate
          </span>
          <span className="flex items-center gap-1.5">
            <span className="size-1.5 rounded-full bg-muted-foreground/40" /> instance stopped,
            takes it on start
          </span>
        </p>
      </div>
    </HubFrame>
  )
}
