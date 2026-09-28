import { Alert, AlertAction, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import { CircleXIcon } from "lucide-react"

/** A save refused because `file` changed since it was read, with "Load theirs" to read it again. */
export function StaleNotice({ file, onLoad }: { file: string; onLoad: () => void }) {
  return (
    <Alert variant="destructive">
      <CircleXIcon />
      <AlertTitle>Changed since you opened it</AlertTitle>
      <AlertDescription>
        {file} was saved by someone else, most likely the agent. Load theirs to see it. Your edits
        here are dropped.
      </AlertDescription>
      <AlertAction>
        <Button size="sm" variant="outline" onClick={onLoad}>
          Load theirs
        </Button>
      </AlertAction>
    </Alert>
  )
}

export function Failure({ children }: { children: string }) {
  return (
    <Alert variant="destructive">
      <CircleXIcon />
      <AlertTitle>The instance did not answer</AlertTitle>
      <AlertDescription>{children}</AlertDescription>
    </Alert>
  )
}
