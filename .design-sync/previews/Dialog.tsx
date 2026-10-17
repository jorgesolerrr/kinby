import {
  Button,
  Checkbox,
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
  Field,
  FieldDescription,
  FieldGroup,
  FieldLabel,
  Input,
  Textarea,
} from "@kinby/web"

export const AddSecret = () => (
  <Dialog open>
    <DialogTrigger render={<Button variant="outline" size="sm" />}>Add a secret</DialogTrigger>
    <DialogContent>
      <form className="flex flex-col gap-4" onSubmit={(event) => event.preventDefault()}>
        <DialogHeader>
          <DialogTitle>Add a hub secret</DialogTitle>
          <DialogDescription>Its name is how you pick it. Its value is never shown back.</DialogDescription>
        </DialogHeader>
        <FieldGroup>
          <Field>
            <FieldLabel htmlFor="secret-name">Name</FieldLabel>
            <Input id="secret-name" autoComplete="off" defaultValue="GITHUB_TOKEN" />
          </Field>
          <Field>
            <FieldLabel htmlFor="secret-value">Value</FieldLabel>
            <Input id="secret-value" type="password" defaultValue="ghp_placeholdervalue" />
          </Field>
          <Field orientation="horizontal">
            <Checkbox id="secret-recreate" defaultChecked />
            <FieldLabel htmlFor="secret-recreate">Recreate the running ones now</FieldLabel>
          </Field>
        </FieldGroup>
        <DialogFooter>
          <DialogClose render={<Button type="button" variant="outline" />}>Cancel</DialogClose>
          <Button type="submit">Save</Button>
        </DialogFooter>
      </form>
    </DialogContent>
  </Dialog>
)

export const NewRoutine = () => (
  <Dialog open>
    <DialogContent>
      <DialogHeader>
        <DialogTitle>New routine</DialogTitle>
        <DialogDescription>kinby runs it on its own, on the schedule you set.</DialogDescription>
      </DialogHeader>
      <FieldGroup>
        <Field>
          <FieldLabel htmlFor="routine-name">Name</FieldLabel>
          <Input id="routine-name" defaultValue="morning-brief" />
        </Field>
        <Field>
          <FieldLabel htmlFor="routine-prompt">What it does</FieldLabel>
          <Textarea
            id="routine-prompt"
            defaultValue="Read my calendar and inbox, then write a short plan for the day."
          />
          <FieldDescription>Runs every weekday at 8:00.</FieldDescription>
        </Field>
      </FieldGroup>
      <DialogFooter>
        <DialogClose render={<Button variant="outline" />}>Cancel</DialogClose>
        <Button>Create routine</Button>
      </DialogFooter>
    </DialogContent>
  </Dialog>
)

export const Informational = () => (
  <Dialog open>
    <DialogContent>
      <DialogHeader>
        <DialogTitle>Instance restarted</DialogTitle>
        <DialogDescription>
          The research instance picked up the new GITHUB_TOKEN. Threads that were mid-turn resume
          from their last message.
        </DialogDescription>
      </DialogHeader>
      <DialogFooter showCloseButton />
    </DialogContent>
  </Dialog>
)
