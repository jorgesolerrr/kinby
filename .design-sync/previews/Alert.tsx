import { Alert, AlertAction, AlertDescription, AlertTitle, Button } from "@kinby/web"
import { CircleXIcon, InfoIcon, TriangleAlertIcon } from "lucide-react"

export const Default = () => (
  <Alert className="w-[28rem]">
    <TriangleAlertIcon />
    <AlertTitle>Some files did not load</AlertTitle>
    <AlertDescription>
      skills/inbox-triage/SKILL.md: the front matter has no description, so the list leaves it out.
    </AlertDescription>
  </Alert>
)

export const Destructive = () => (
  <Alert variant="destructive" className="w-[28rem]">
    <CircleXIcon />
    <AlertTitle>The instance did not answer</AlertTitle>
    <AlertDescription>
      Research stopped responding after 30 seconds. Check that it is running, then try again.
    </AlertDescription>
  </Alert>
)

export const WithAction = () => (
  <div className="flex w-[28rem] flex-col gap-3">
    <Alert>
      <TriangleAlertIcon />
      <AlertTitle>This instance runs an older core</AlertTitle>
      <AlertDescription>Its core does not have this yet. Update it from Core version.</AlertDescription>
      <AlertAction>
        <Button size="sm" variant="outline">
          Update
        </Button>
      </AlertAction>
    </Alert>
    <Alert variant="destructive">
      <CircleXIcon />
      <AlertTitle>Changed since you opened it</AlertTitle>
      <AlertDescription>
        MEMORY.md was saved by the agent. Load theirs to see it; your edits here are dropped.
      </AlertDescription>
      <AlertAction>
        <Button size="sm" variant="outline">
          Load theirs
        </Button>
      </AlertAction>
    </Alert>
  </div>
)

export const TitleOnly = () => (
  <div className="flex w-[28rem] flex-col gap-3">
    <Alert>
      <InfoIcon />
      <AlertTitle>Routines pause while the instance is stopped</AlertTitle>
    </Alert>
    <Alert>
      <AlertTitle>Secret not read yet</AlertTitle>
      <AlertDescription>GITHUB_TOKEN is set, but no skill on this instance reads it.</AlertDescription>
    </Alert>
  </div>
)
