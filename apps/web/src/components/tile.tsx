import { Card, CardContent, CardDescription, CardHeader } from "@/components/ui/card"

/** One figure over a range, under its name and over a note that breaks it down. */
export function Tile({ title, value, note }: { title: string; value: string; note: string }) {
  return (
    <section aria-label={title}>
      <Card size="sm">
        <CardHeader>
          <CardDescription>{title}</CardDescription>
        </CardHeader>
        <CardContent>
          <p className="text-2xl font-semibold tabular-nums">{value}</p>
          <p className="text-xs text-muted-foreground">{note}</p>
        </CardContent>
      </Card>
    </section>
  )
}
