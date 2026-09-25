import { useActionState } from "react"

import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Field, FieldError, FieldGroup, FieldLabel } from "@/components/ui/field"
import { Input } from "@/components/ui/input"

/** Exchange the access token for a browser session. A refusal says nothing about the token. */
export function SignIn({ onSignIn }: { onSignIn: (token: string) => Promise<boolean> }) {
  const [failed, signIn, pending] = useActionState(async (_failed: boolean, form: FormData) => {
    const token = form.get("token")
    return !(typeof token === "string" && (await onSignIn(token)))
  }, false)

  return (
    <main className="flex min-h-svh items-center justify-center p-6">
      <Card className="w-full max-w-sm">
        <CardHeader>
          <CardTitle>Sign in to kinby</CardTitle>
          <CardDescription>
            Paste the access token your hub printed on its first start.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <form action={signIn}>
            <FieldGroup>
              <Field data-invalid={failed || undefined}>
                <FieldLabel htmlFor="token">Access token</FieldLabel>
                <Input
                  id="token"
                  name="token"
                  type="password"
                  autoComplete="current-password"
                  required
                  aria-invalid={failed || undefined}
                />
                {failed && <FieldError>Could not sign in.</FieldError>}
              </Field>
              <Button type="submit" disabled={pending}>
                Sign in
              </Button>
            </FieldGroup>
          </form>
        </CardContent>
      </Card>
    </main>
  )
}
