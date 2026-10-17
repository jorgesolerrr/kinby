import { Separator } from "@kinby/web"

export const Horizontal = () => (
  <div className="flex w-80 flex-col gap-3 text-sm">
    <div>
      <div className="font-medium">Research</div>
      <div className="text-muted-foreground">Reads papers and keeps notes in memory.</div>
    </div>
    <Separator />
    <div>
      <div className="font-medium">Accounts</div>
      <div className="text-muted-foreground">Pays invoices and files receipts.</div>
    </div>
  </div>
)

export const Vertical = () => (
  <div className="flex h-5 items-center gap-3 text-sm">
    <span>Threads</span>
    <Separator orientation="vertical" />
    <span>Routines</span>
    <Separator orientation="vertical" />
    <span>Skills</span>
    <Separator orientation="vertical" />
    <span>Memory</span>
  </div>
)

export const InStats = () => (
  <div className="flex w-fit items-center gap-4 rounded-xl border bg-card p-4 text-sm">
    <div className="flex flex-col">
      <span className="text-muted-foreground">Threads</span>
      <span className="font-heading text-lg font-semibold">128</span>
    </div>
    <Separator orientation="vertical" />
    <div className="flex flex-col">
      <span className="text-muted-foreground">Tokens</span>
      <span className="font-heading text-lg font-semibold">1.2M</span>
    </div>
    <Separator orientation="vertical" />
    <div className="flex flex-col">
      <span className="text-muted-foreground">Spend</span>
      <span className="font-heading text-lg font-semibold">$42.18</span>
    </div>
  </div>
)
