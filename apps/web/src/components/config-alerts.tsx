import { Alert, AlertAction, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import { CircleXIcon } from "lucide-react"

/** A call the instance did not answer, or refused, with its reason. */
export function Failure({
  title = "The instance did not answer",
  children,
}: {
  title?: string
  children: string
}) {
  return (
    <Alert variant="destructive">
      <CircleXIcon />
      <AlertTitle>{title}</AlertTitle>
      <AlertDescription>{children}</AlertDescription>
    </Alert>
  )
}

/** A save refused because `file` changed since it was read. "Load theirs" reads it again. */
export function StaleAlert({ file, onLoad }: { file: string; onLoad: () => void }) {
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
