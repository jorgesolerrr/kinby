// PROTOTYPE: the specimen frame each component variant lays its pieces in, and the state words they
// share. Only the frame is shared; every variant draws its components its own way.
import {
  CircleCheckIcon,
  CircleDashedIcon,
  CirclePauseIcon,
  LoaderCircleIcon,
  type LucideIcon,
} from "lucide-react"
import type { ReactNode } from "react"

import type { ProtoInstance } from "./fixtures"

export function Sheet({
  title,
  intent,
  children,
}: {
  title: string
  intent: string
  children: ReactNode
}) {
  return (
    <main className="mx-auto flex w-full max-w-5xl flex-col gap-12 px-4 pt-10 pb-28 md:px-8">
      <header className="flex flex-col gap-2">
        <p className="text-xs font-medium tracking-wide text-muted-foreground uppercase">
          Prototype · components
        </p>
        <h1 className="text-3xl font-semibold">{title}</h1>
        <p className="max-w-prose text-muted-foreground">{intent}</p>
      </header>
      {children}
    </main>
  )
}

export function Specimen({
  name,
  note,
  children,
}: {
  name: string
  note?: string
  children: ReactNode
}) {
  return (
    <section aria-label={name} className="flex flex-col gap-4">
      <div className="flex items-baseline justify-between gap-4 border-b pb-2">
        <h2 className="text-lg font-semibold">{name}</h2>
        {note && <p className="text-xs text-muted-foreground">{note}</p>}
      </div>
      {children}
    </section>
  )
}

export const STATE: Record<ProtoInstance["state"], { label: string; icon: LucideIcon }> = {
  running: { label: "Running", icon: CircleCheckIcon },
  starting: { label: "Starting", icon: LoaderCircleIcon },
  stopped: { label: "Stopped", icon: CirclePauseIcon },
  setup: { label: "Setup pending", icon: CircleDashedIcon },
}

export function initial(name: string) {
  return name.charAt(0).toUpperCase()
}
