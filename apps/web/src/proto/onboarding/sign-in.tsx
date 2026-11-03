// PROTOTYPE · The hub sign-in, two variants. A "Bare": no card, the mark over one token box with
// its submit inside, like the composer. B "Split": a brand panel beside a plain form that names
// the hub it signs in to. Type "wrong" to see the refusal; anything else signs in.
import { ArrowRightIcon, ChevronRightIcon } from "lucide-react"
import { type FormEvent, useState } from "react"

import { KinbyMark } from "@/components/kinby-mark"
import { Button } from "@/components/ui/button"
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible"
import { Field, FieldDescription, FieldError, FieldGroup, FieldLabel } from "@/components/ui/field"
import { Input } from "@/components/ui/input"
import {
  InputGroup,
  InputGroupAddon,
  InputGroupButton,
  InputGroupInput,
} from "@/components/ui/input-group"
import { Spinner } from "@/components/ui/spinner"

const HUB = "hub.home.arpa:8080"
const ROTATE = "uv run kinby hub .kinby/hub token rotate"

function useSignIn(onSignedIn: () => void) {
  const [token, setToken] = useState("")
  const [pending, setPending] = useState(false)
  const [failed, setFailed] = useState(false)
  const submit = (e: FormEvent) => {
    e.preventDefault()
    setPending(true)
    setFailed(false)
    window.setTimeout(() => {
      setPending(false)
      if (token.trim() === "wrong") setFailed(true)
      else onSignedIn()
    }, 700)
  }
  return { token, setToken, pending, failed, submit }
}

const REFUSAL = "That token didn't sign in. Check it and that the hub is reachable."

function Lost() {
  return (
    <Collapsible>
      <CollapsibleTrigger className="group inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
        <ChevronRightIcon className="size-3.5 transition-transform group-data-[panel-open]:rotate-90" />
        Lost the token?
      </CollapsibleTrigger>
      <CollapsibleContent>
        <p className="mt-2 text-sm text-muted-foreground">
          The hub prints it once. On the hub's machine, run{" "}
          <code className="rounded bg-muted px-1 py-0.5 font-mono text-xs text-foreground">
            {ROTATE}
          </code>{" "}
          for a new one. Every browser signed in with the old one signs out.
        </p>
      </CollapsibleContent>
    </Collapsible>
  )
}

export function SignInA({ onSignedIn }: { onSignedIn: () => void }) {
  const s = useSignIn(onSignedIn)
  return (
    <main className="grid min-h-svh place-items-center bg-background px-4">
      <div className="flex w-full max-w-sm flex-col gap-8 pb-[10svh]">
        <div className="flex flex-col items-center gap-4 text-center">
          <KinbyMark className="size-12" />
          <div className="flex flex-col gap-1">
            <h1 className="font-heading text-2xl font-semibold">Sign in to kinby</h1>
            <p className="text-sm text-muted-foreground">
              Paste the access token your hub printed on its first start.
            </p>
          </div>
        </div>
        <form onSubmit={s.submit} className="flex flex-col gap-3">
          <Field data-invalid={s.failed || undefined}>
            <FieldLabel htmlFor="proto-token" className="sr-only">
              Access token
            </FieldLabel>
            <InputGroup className="h-11">
              <InputGroupInput
                id="proto-token"
                type="password"
                autoComplete="current-password"
                placeholder="Access token"
                autoFocus
                value={s.token}
                aria-invalid={s.failed || undefined}
                onChange={(e) => s.setToken(e.target.value)}
              />
              <InputGroupAddon align="inline-end">
                <InputGroupButton
                  type="submit"
                  variant="default"
                  size="icon-sm"
                  aria-label="Sign in"
                  disabled={s.pending || s.token.trim() === ""}
                >
                  {s.pending ? <Spinner /> : <ArrowRightIcon />}
                </InputGroupButton>
              </InputGroupAddon>
            </InputGroup>
            {s.failed && <FieldError>{REFUSAL}</FieldError>}
          </Field>
          <Lost />
        </form>
        <p className="text-center font-mono text-xs text-muted-foreground">{HUB}</p>
      </div>
    </main>
  )
}

export function SignInB({ onSignedIn }: { onSignedIn: () => void }) {
  const s = useSignIn(onSignedIn)
  return (
    <main className="grid min-h-svh bg-background md:grid-cols-2">
      <div className="flex flex-col justify-between gap-8 bg-muted p-6 md:p-10">
        <div className="flex items-center gap-2">
          <KinbyMark className="size-8" />
          <span className="font-heading text-xl font-semibold tracking-tight">kinby</span>
        </div>
        <p className="hidden max-w-sm font-reading text-2xl leading-snug md:block">
          A teammate that remembers, acts on your behalf, and shows up when it matters.
        </p>
        <p className="hidden text-xs text-muted-foreground md:block">Self-hosted · one user</p>
      </div>
      <div className="flex items-center justify-center px-4 py-10 md:px-10">
        <form onSubmit={s.submit} className="flex w-full max-w-sm flex-col gap-6">
          <div className="flex flex-col gap-1">
            <h1 className="font-heading text-2xl font-semibold">Sign in</h1>
            <p className="text-sm text-muted-foreground">
              to the hub at <span className="font-mono text-foreground">{HUB}</span>
            </p>
          </div>
          <FieldGroup>
            <Field data-invalid={s.failed || undefined}>
              <FieldLabel htmlFor="proto-token-b">Access token</FieldLabel>
              <Input
                id="proto-token-b"
                type="password"
                autoComplete="current-password"
                autoFocus
                value={s.token}
                aria-invalid={s.failed || undefined}
                onChange={(e) => s.setToken(e.target.value)}
              />
              {s.failed ? (
                <FieldError>{REFUSAL}</FieldError>
              ) : (
                <FieldDescription>The hub printed it on its first start.</FieldDescription>
              )}
            </Field>
            <Button type="submit" disabled={s.pending || s.token.trim() === ""}>
              {s.pending && <Spinner data-icon="inline-start" />}
              Sign in
            </Button>
          </FieldGroup>
          <Lost />
        </form>
      </div>
    </main>
  )
}
