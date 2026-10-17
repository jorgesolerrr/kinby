// PROTOTYPE secrets C, "List and detail": the same split as Library A, so the hub pages share one
// shape. The names on the left; the open secret on the right with each linked agent on its own
// row, the replace form always there, and Delete set apart at the foot.
import { PlusIcon, SearchIcon } from "lucide-react"
import { useState } from "react"

import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { Field, FieldDescription, FieldGroup, FieldLabel } from "@/components/ui/field"
import { Input } from "@/components/ui/input"
import { InputGroup, InputGroupAddon, InputGroupInput } from "@/components/ui/input-group"
import { Separator } from "@/components/ui/separator"

import { instances, secrets } from "./fixtures"
import { AgentAvatar, HubFrame } from "./parts"

export const name = "List and detail"

export function SecretsC() {
  const [openName, setOpenName] = useState("ANTHROPIC_API_KEY")
  const [query, setQuery] = useState("")
  const shown = secrets.filter((secret) => secret.name.toLowerCase().includes(query.toLowerCase()))
  const open = secrets.find((secret) => secret.name === openName) ?? secrets[0]
  const running = open.links.filter((link) => link.running)

  return (
    <HubFrame active="Secrets">
      <div className="flex h-full">
        <section aria-label="Secrets" className="flex w-80 shrink-0 flex-col border-r">
          <div className="flex flex-col gap-3 border-b p-4">
            <div className="flex items-center gap-2">
              <h1 className="flex-1 font-heading text-xl font-semibold">Secrets</h1>
              <Button size="sm">
                <PlusIcon data-icon="inline-start" />
                Add
              </Button>
            </div>
            <InputGroup>
              <InputGroupInput
                aria-label="Search secrets"
                placeholder="Search secrets"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
              />
              <InputGroupAddon>
                <SearchIcon />
              </InputGroupAddon>
            </InputGroup>
          </div>
          <ul className="flex flex-col gap-0.5 overflow-y-auto p-2">
            {shown.map((secret) => (
              <li key={secret.name}>
                <button
                  type="button"
                  aria-current={secret.name === open.name ? "page" : undefined}
                  onClick={() => setOpenName(secret.name)}
                  className="flex w-full items-center gap-2 rounded-md px-3 py-2 text-left outline-none hover:bg-accent focus-visible:ring-3 focus-visible:ring-ring/50 aria-[current=page]:bg-accent"
                >
                  <span className="min-w-0 flex-1 truncate font-mono text-sm">{secret.name}</span>
                  <span className="font-mono text-xs text-muted-foreground">
                    {secret.links.length === 0 ? "unused" : `${secret.links.length} linked`}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </section>

        <section aria-label={open.name} className="flex min-w-0 flex-1 flex-col gap-8 px-8 py-6">
          <header className="flex flex-col gap-1">
            <h2 className="font-mono text-xl font-semibold">{open.name}</h2>
            <p className="text-sm text-muted-foreground">
              The hub keeps the value. It never shows it back.
            </p>
          </header>

          <section aria-label="Linked instances" className="flex flex-col gap-2">
            <h3 className="font-medium">Linked to</h3>
            {open.links.length === 0 ? (
              <p className="text-sm text-muted-foreground">
                No instance reads it yet. Link it from an instance&apos;s settings.
              </p>
            ) : (
              <ul className="flex flex-col divide-y rounded-lg border">
                {open.links.map((link) => {
                  const agent = instances.find((each) => each.name === link.agent) ?? instances[0]
                  return (
                    <li key={link.agent} className="flex items-center gap-3 px-4 py-3">
                      <AgentAvatar agent={agent} size="sm" />
                      <span className="w-28 text-sm font-medium">{agent.name}</span>
                      <span className="flex-1 font-mono text-xs text-muted-foreground">
                        {link.field}
                      </span>
                      <span className="text-xs text-muted-foreground">
                        {link.running ? "Running" : "Stopped"}
                      </span>
                    </li>
                  )
                })}
              </ul>
            )}
          </section>

          <form className="flex max-w-xl flex-col gap-3" aria-label="Replace the value">
            <h3 className="font-medium">Replace the value</h3>
            <FieldGroup>
              <Field>
                <FieldLabel htmlFor="new-value">New value</FieldLabel>
                <Input id="new-value" type="password" autoComplete="new-password" />
                <FieldDescription>
                  {open.links.length === 0
                    ? "It takes the old one's place."
                    : `It takes the old one's place in ${open.links.map((link) => link.agent).join(", ")}.`}
                </FieldDescription>
              </Field>
              {running.length > 0 && (
                <Field orientation="horizontal">
                  <Checkbox id="recreate" defaultChecked />
                  <FieldLabel htmlFor="recreate" className="font-normal">
                    Recreate {running.map((link) => link.agent).join(" and ")} now
                  </FieldLabel>
                </Field>
              )}
            </FieldGroup>
            <Button type="button" className="self-start">
              Save
            </Button>
          </form>

          <div className="mt-auto flex flex-col gap-3">
            <Separator />
            <div className="flex items-center gap-4">
              <p className="flex-1 text-sm text-muted-foreground">
                Deleting drops the hub&apos;s copy. Linked instances keep the value they hold, and a
                token stays valid at its provider.
              </p>
              <Button variant="destructive">Delete {open.name}</Button>
            </div>
          </div>
        </section>
      </div>
    </HubFrame>
  )
}
