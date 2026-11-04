import { ArrowRightIcon, ChevronRightIcon } from "lucide-react"
import { useActionState, useState } from "react"

import { KinbyMark } from "@/components/kinby-mark"
import { Button } from "@/components/ui/button"
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible"
import { Field, FieldError, FieldLabel } from "@/components/ui/field"
import {
  InputGroup,
  InputGroupAddon,
  InputGroupButton,
  InputGroupInput,
} from "@/components/ui/input-group"
import { Spinner } from "@/components/ui/spinner"

const focus = (input: HTMLInputElement | null) => input?.focus()

/** Exchange the access token for a browser session. A refusal says nothing about the token. */
export function SignIn({ onSignIn }: { onSignIn: (token: string) => Promise<boolean> }) {
  const [token, setToken] = useState("")
  const [failed, signIn, pending] = useActionState(async () => !(await onSignIn(token)), false)

  return (
    <main className="grid min-h-svh place-items-center px-4">
      <div className="flex w-full max-w-sm flex-col gap-8">
        <div className="flex flex-col items-center gap-4 text-center">
          <KinbyMark className="size-12" />
          <div className="flex flex-col gap-1">
            <h1 className="font-heading text-2xl font-semibold">Sign in to kinby</h1>
            <p className="text-sm text-muted-foreground">
              Paste the access token your hub printed on its first start.
            </p>
          </div>
        </div>
        <form action={signIn} className="flex flex-col gap-3">
          <Field data-invalid={failed || undefined}>
            <FieldLabel htmlFor="token" className="sr-only">
              Access token
            </FieldLabel>
            <InputGroup className="h-11">
              <InputGroupInput
                id="token"
                name="token"
                type="password"
                autoComplete="current-password"
                placeholder="Access token"
                ref={focus}
                value={token}
                aria-invalid={failed || undefined}
                onChange={(e) => setToken(e.target.value)}
              />
              <InputGroupAddon align="inline-end">
                <InputGroupButton
                  type="submit"
                  variant="default"
                  size="icon-sm"
                  aria-label="Sign in"
                  disabled={pending || token.trim() === ""}
                >
                  {pending ? <Spinner /> : <ArrowRightIcon />}
                </InputGroupButton>
              </InputGroupAddon>
            </InputGroup>
            {failed && (
              <FieldError>
                Could not sign in. Check the token and that the hub is reachable, then sign in
                again.
              </FieldError>
            )}
          </Field>
          <Collapsible>
            <CollapsibleTrigger render={<Button variant="ghost" size="sm" />}>
              <ChevronRightIcon
                data-icon="inline-start"
                className="transition-transform group-data-[panel-open]/button:rotate-90"
              />
              Lost the token?
            </CollapsibleTrigger>
            <CollapsibleContent>
              <p className="mt-2 text-sm text-muted-foreground">
                The hub prints it once. On the hub's machine, run{" "}
                <code className="font-mono text-foreground">
                  uv run kinby hub .kinby/hub token rotate
                </code>{" "}
                for a new one. Every browser signed in with the old one signs out.
              </p>
            </CollapsibleContent>
          </Collapsible>
        </form>
        <p className="text-center font-mono text-xs text-muted-foreground">
          {window.location.host}
        </p>
      </div>
    </main>
  )
}
