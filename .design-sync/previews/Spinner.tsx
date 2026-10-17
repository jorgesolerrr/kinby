import { Badge, Button, Spinner } from "@kinby/web"

export const Sizes = () => (
  <div className="flex items-center gap-4">
    <Spinner className="size-3.5" />
    <Spinner />
    <Spinner className="size-6" />
    <Spinner className="size-8" />
  </div>
)

export const InButtons = () => (
  <div className="flex flex-wrap items-center gap-2">
    <Button disabled>
      <Spinner data-icon="inline-start" />
      Starting…
    </Button>
    <Button variant="outline" disabled>
      <Spinner data-icon="inline-start" />
      Saving routine
    </Button>
    <Badge variant="secondary">
      <Spinner data-icon="inline-start" />
      Installing
    </Badge>
  </div>
)

export const Status = () => (
  <div className="flex w-80 flex-col gap-3 text-sm">
    <div className="flex items-center gap-2 text-muted-foreground">
      <Spinner />
      <span>Research is starting</span>
    </div>
    <div className="flex items-center gap-2 text-muted-foreground">
      <Spinner />
      <span>Pulling the core image</span>
    </div>
    <div className="flex items-center gap-2 text-primary">
      <Spinner />
      <span>Working</span>
    </div>
  </div>
)
