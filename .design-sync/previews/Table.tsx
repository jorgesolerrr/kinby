import {
  Badge,
  Table,
  TableBody,
  TableCaption,
  TableCell,
  TableFooter,
  TableHead,
  TableHeader,
  TableRow,
} from "@kinby/web"

export const UsageBySource = () => (
  <Table aria-label="Usage by source">
    <TableHeader>
      <TableRow>
        <TableHead>Source</TableHead>
        <TableHead className="text-right">Runs</TableHead>
        <TableHead className="text-right">Tokens</TableHead>
        <TableHead className="text-right">Time</TableHead>
        <TableHead className="text-right">Cost</TableHead>
      </TableRow>
    </TableHeader>
    <TableBody>
      <TableRow>
        <TableCell>Anthropic API</TableCell>
        <TableCell className="text-right">—</TableCell>
        <TableCell className="text-right">1.82M</TableCell>
        <TableCell className="text-right">—</TableCell>
        <TableCell className="text-right">$42.18</TableCell>
      </TableRow>
      <TableRow>
        <TableCell>Claude plan</TableCell>
        <TableCell className="text-right">31</TableCell>
        <TableCell className="text-right">964K</TableCell>
        <TableCell className="text-right">2h 14m</TableCell>
        <TableCell className="text-right">not priced</TableCell>
      </TableRow>
      <TableRow>
        <TableCell>ChatGPT plan</TableCell>
        <TableCell className="text-right">8</TableCell>
        <TableCell className="text-right">212K</TableCell>
        <TableCell className="text-right">36m</TableCell>
        <TableCell className="text-right">not priced</TableCell>
      </TableRow>
    </TableBody>
  </Table>
)

const INSTANCES = [
  ["home", "Running", "success", "128", "$18.40"],
  ["coder", "Running", "success", "54", "$21.02"],
  ["finance", "Needs you", "attention", "12", "$2.76"],
  ["travel", "Stopped", "outline", "3", "$0.00"],
] as const

export const Instances = () => (
  <Table aria-label="Spend by instance">
    <TableCaption>Spend this month, UTC.</TableCaption>
    <TableHeader>
      <TableRow>
        <TableHead>Instance</TableHead>
        <TableHead>State</TableHead>
        <TableHead className="text-right">Threads</TableHead>
        <TableHead className="text-right">Cost</TableHead>
      </TableRow>
    </TableHeader>
    <TableBody>
      {INSTANCES.map(([name, state, variant, threads, cost]) => (
        <TableRow key={name}>
          <TableCell className="font-medium">{name}</TableCell>
          <TableCell>
            <Badge variant={variant}>{state}</Badge>
          </TableCell>
          <TableCell className="text-right tabular-nums">{threads}</TableCell>
          <TableCell className="text-right tabular-nums">{cost}</TableCell>
        </TableRow>
      ))}
    </TableBody>
    <TableFooter>
      <TableRow>
        <TableCell colSpan={3}>Total</TableCell>
        <TableCell className="text-right tabular-nums">$42.18</TableCell>
      </TableRow>
    </TableFooter>
  </Table>
)

export const Selected = () => (
  <Table aria-label="Secrets">
    <TableHeader>
      <TableRow>
        <TableHead>Secret</TableHead>
        <TableHead>Used by</TableHead>
        <TableHead className="text-right">Updated</TableHead>
      </TableRow>
    </TableHeader>
    <TableBody>
      <TableRow>
        <TableCell className="font-mono">GITHUB_TOKEN</TableCell>
        <TableCell>coder</TableCell>
        <TableCell className="text-right text-muted-foreground">3 days ago</TableCell>
      </TableRow>
      <TableRow data-state="selected">
        <TableCell className="font-mono">GOOGLE_CALENDAR_KEY</TableCell>
        <TableCell>home, travel</TableCell>
        <TableCell className="text-right text-muted-foreground">2 weeks ago</TableCell>
      </TableRow>
      <TableRow>
        <TableCell className="font-mono">BANK_EXPORT_PASSPHRASE</TableCell>
        <TableCell>finance</TableCell>
        <TableCell className="text-right text-muted-foreground">Mar 2</TableCell>
      </TableRow>
    </TableBody>
  </Table>
)
