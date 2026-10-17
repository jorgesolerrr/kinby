import {
  Badge,
  Button,
  Field,
  FieldGroup,
  FieldLabel,
  Input,
  Separator,
  Sheet,
  SheetClose,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
  Textarea,
} from "@kinby/web"

export const EditSkill = () => (
  <Sheet open>
    <SheetTrigger render={<Button variant="outline" />}>Edit skill</SheetTrigger>
    <SheetContent>
      <SheetHeader>
        <SheetTitle>Edit skill</SheetTitle>
        <SheetDescription>Changes apply to every instance that loads it.</SheetDescription>
      </SheetHeader>
      <FieldGroup className="px-4">
        <Field>
          <FieldLabel htmlFor="skill-name">Name</FieldLabel>
          <Input id="skill-name" defaultValue="weekly-review" />
        </Field>
        <Field>
          <FieldLabel htmlFor="skill-description">Description</FieldLabel>
          <Textarea
            id="skill-description"
            defaultValue="Summarise the week's threads and routines, then list open follow-ups."
          />
        </Field>
      </FieldGroup>
      <SheetFooter>
        <Button>Save changes</Button>
        <SheetClose render={<Button variant="outline" />}>Cancel</SheetClose>
      </SheetFooter>
    </SheetContent>
  </Sheet>
)

export const MemoryDetails = () => (
  <Sheet open>
    <SheetContent side="left">
      <SheetHeader>
        <SheetTitle>Memory</SheetTitle>
        <SheetDescription>What kinby remembers about you.</SheetDescription>
      </SheetHeader>
      <div className="flex flex-col gap-3 px-4">
        {[
          ["Prefers mornings for deep work", "Learned 3 days ago"],
          ["Ana is the design lead on the hub project", "Learned last week"],
          ["Pays invoices on the 1st", "Learned in March"],
        ].map(([fact, when]) => (
          <div key={fact} className="flex flex-col gap-1">
            <span className="font-medium">{fact}</span>
            <span className="text-muted-foreground">{when}</span>
            <Separator className="mt-2" />
          </div>
        ))}
      </div>
    </SheetContent>
  </Sheet>
)

export const UsageBottom = () => (
  <Sheet open>
    <SheetContent side="bottom">
      <SheetHeader>
        <SheetTitle>Usage this month</SheetTitle>
        <SheetDescription>Across all instances, reset on the 1st.</SheetDescription>
      </SheetHeader>
      <div className="flex flex-wrap items-center gap-2 px-4" style={{ paddingBottom: "1rem" }}>
        <Badge variant="secondary">research · $18.40</Badge>
        <Badge variant="secondary">inbox · $15.02</Badge>
        <Badge variant="secondary">coder · $8.76</Badge>
        <Badge>Total $42.18</Badge>
      </div>
    </SheetContent>
  </Sheet>
)
