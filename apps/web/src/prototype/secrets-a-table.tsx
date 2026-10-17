// PROTOTYPE secrets A, "Table with an add row": one full-width table, and adding a secret is the
// table's first row, not a dialog. Each secret lists the agents linked to it as chips with the
// field, and Replace opens in place under its row.
import { KeyRoundIcon, PlusIcon, Trash2Icon } from "lucide-react"
import { Fragment, useState } from "react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"

import { secrets } from "./fixtures"
import { HubFrame } from "./parts"

export const name = "Table with an add row"

export function SecretsA() {
  const [replacing, setReplacing] = useState<string>()
  return (
    <HubFrame active="Secrets">
      <div className="flex flex-col gap-4 px-8 py-6">
        <header className="flex flex-col gap-1">
          <h1 className="font-heading text-2xl font-semibold">Secrets</h1>
          <p className="text-sm text-muted-foreground">
            Set a value once and link it to any instance. The hub never shows a value back.
          </p>
        </header>
        <div className="rounded-lg border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-72 pl-4">Name</TableHead>
                <TableHead>Linked to</TableHead>
                <TableHead className="w-48 pr-4 text-right" />
              </TableRow>
            </TableHeader>
            <TableBody>
              <TableRow className="bg-muted/40 hover:bg-muted/40">
                <TableCell className="pl-4">
                  <Input aria-label="Name" placeholder="NEW_SECRET_NAME" className="font-mono" />
                </TableCell>
                <TableCell>
                  <Input aria-label="Value" type="password" placeholder="Value" />
                </TableCell>
                <TableCell className="pr-4 text-right">
                  <Button>
                    <PlusIcon data-icon="inline-start" />
                    Add secret
                  </Button>
                </TableCell>
              </TableRow>
              {secrets.map((secret) => (
                <Fragment key={secret.name}>
                  <TableRow>
                    <TableCell className="pl-4">
                      <span className="flex items-center gap-2 font-mono font-medium">
                        <KeyRoundIcon className="size-4 text-muted-foreground" />
                        {secret.name}
                      </span>
                    </TableCell>
                    <TableCell>
                      {secret.links.length === 0 ? (
                        <span className="text-muted-foreground">Not linked</span>
                      ) : (
                        <span className="flex flex-wrap gap-1.5">
                          {secret.links.map((link) => (
                            <Badge key={link.agent} variant="outline" className="h-6 gap-1.5">
                              {link.agent}
                              <span className="font-mono font-normal text-muted-foreground">
                                {link.field}
                              </span>
                            </Badge>
                          ))}
                        </span>
                      )}
                    </TableCell>
                    <TableCell className="pr-4 text-right">
                      <Button
                        variant="outline"
                        size="sm"
                        aria-expanded={replacing === secret.name}
                        onClick={() =>
                          setReplacing(replacing === secret.name ? undefined : secret.name)
                        }
                      >
                        Replace
                      </Button>
                      <Button variant="ghost" size="icon-sm" aria-label={`Delete ${secret.name}`}>
                        <Trash2Icon />
                      </Button>
                    </TableCell>
                  </TableRow>
                  {replacing === secret.name && (
                    <TableRow className="hover:bg-transparent">
                      <TableCell colSpan={3}>
                        <div className="flex flex-wrap items-center gap-3 px-2 py-1.5">
                          <Input
                            aria-label={`New value for ${secret.name}`}
                            type="password"
                            placeholder="New value"
                            className="max-w-sm"
                            autoFocus
                          />
                          {secret.links.some((link) => link.running) && (
                            <Label>
                              <Checkbox defaultChecked />
                              Recreate{" "}
                              {secret.links
                                .filter((link) => link.running)
                                .map((link) => link.agent)
                                .join(" and ")}{" "}
                              now
                            </Label>
                          )}
                          <span className="ml-auto flex gap-2">
                            <Button variant="ghost" onClick={() => setReplacing(undefined)}>
                              Cancel
                            </Button>
                            <Button>Save</Button>
                          </span>
                        </div>
                      </TableCell>
                    </TableRow>
                  )}
                </Fragment>
              ))}
            </TableBody>
          </Table>
        </div>
      </div>
    </HubFrame>
  )
}
