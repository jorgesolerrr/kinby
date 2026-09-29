import type { Clock, InstanceClient, ToolListResult, ToolRule } from "@kinby/contract"
import { useEffect, useState } from "react"

import { Failure, Warnings } from "@/components/config-alerts"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { usePace } from "@/hooks/use-pace"
import { retried } from "@/lib/operation"

type Caller = Pick<InstanceClient, "call">

const RULES: Record<ToolRule, string> = {
  allow: "Allow",
  ask: "Ask",
  deny: "Deny",
  mode: "Follows the mode",
}

/** Every tool the instance has, read-only. A rule changes in Permissions. */
export function ToolsSection({
  client,
  clock,
  onOpenPermissions,
}: {
  client: Caller
  clock: Clock
  /** Opens Permissions, when that section is available. */
  onOpenPermissions?: () => void
}) {
  const [listed, setListed] = useState<ToolListResult>()
  const [failure, setFailure] = useState<unknown>()
  const pacing = usePace(clock)

  useEffect(() => {
    retried(() => client.call("tool.list", {}), pacing).then(setListed, (error: unknown) =>
      setFailure(error),
    )
  }, [client, pacing])

  if (listed === undefined) {
    return failure === undefined ? (
      <Skeleton className="h-72 w-full" />
    ) : (
      <Failure error={failure} />
    )
  }
  return (
    <div className="flex flex-col gap-4">
      <Warnings warnings={listed.warnings} />
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-sm text-muted-foreground">
          A tool follows the mode unless Permissions gives it its own rule.
        </p>
        <Button
          variant="link"
          size="sm"
          disabled={onOpenPermissions === undefined}
          onClick={onOpenPermissions}
        >
          Change a rule in Permissions
        </Button>
      </div>
      <Table aria-label="Tools">
        <TableHeader>
          <TableRow>
            <TableHead>Tool</TableHead>
            <TableHead>Source</TableHead>
            <TableHead>Access</TableHead>
            <TableHead>Rule</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {listed.tools.map((tool) => (
            <TableRow key={tool.name}>
              <TableCell>{tool.name}</TableCell>
              <TableCell>{tool.source}</TableCell>
              <TableCell>{tool.write ? "Writes" : "Reads"}</TableCell>
              <TableCell>
                <Badge variant={tool.rule === "mode" ? "secondary" : "outline"}>
                  {RULES[tool.rule]}
                </Badge>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  )
}
