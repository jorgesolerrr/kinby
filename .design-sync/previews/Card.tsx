import {
  Button,
  Card,
  CardAction,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@kinby/web"

export const Default = () => (
  <Card className="w-80">
    <CardHeader>
      <CardTitle>Morning brief</CardTitle>
      <CardDescription>Runs every weekday at 8:00</CardDescription>
      <CardAction>
        <Button variant="ghost" size="sm">
          Edit
        </Button>
      </CardAction>
    </CardHeader>
    <CardContent>
      <p>
        Reads your calendar and inbox, then writes a short plan for the day with anything that
        needs a reply before noon.
      </p>
    </CardContent>
    <CardFooter className="justify-between">
      <span className="text-muted-foreground">Last run 2 hours ago</span>
      <Button size="sm">Run now</Button>
    </CardFooter>
  </Card>
)

export const Small = () => (
  <Card size="sm" className="w-64">
    <CardHeader>
      <CardTitle>Spend this month</CardTitle>
      <CardDescription>Across all instances</CardDescription>
    </CardHeader>
    <CardContent>
      <div className="font-heading text-2xl font-semibold">$42.18</div>
    </CardContent>
  </Card>
)

export const Stat = () => (
  <div className="flex gap-3">
    {[
      ["Threads", "128"],
      ["Routines", "6"],
      ["Skills", "14"],
    ].map(([label, value]) => (
      <Card key={label} size="sm" className="w-32">
        <CardHeader>
          <CardDescription>{label}</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="font-heading text-2xl font-semibold">{value}</div>
        </CardContent>
      </Card>
    ))}
  </div>
)
