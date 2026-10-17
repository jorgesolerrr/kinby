# kinby design system: how to build with it

kinby is a self-hosted personal AI teammate. Its UI is shadcn/ui on Base UI (preset `base-nova`), styled with Tailwind v4 utility classes over CSS custom-property tokens. Every component is on `window.Kinby` (e.g. `Kinby.Button`, `Kinby.CardHeader`).

## Setup

- No provider is needed for most components. Exceptions: `Sidebar` needs `SidebarProvider` around the layout; `Tooltip` needs `TooltipProvider`; `MessageScroller` needs `MessageScrollerProvider`.
- Light theme is the default. Dark theme: put `class="dark"` on `<html>` (or any ancestor). Every token flips; never hard-code a color for dark.
- Body text is Geist (`font-sans`), headings are Gabarito (`font-heading`, applied to h1-h4 automatically), code is Geist Mono (`font-mono`).

## Styling idiom: Tailwind utilities on kinby tokens

Style your own layout glue with utilities whose names are kinby's tokens. Use these, never raw hex or Tailwind palette colors (`bg-green-600`, `text-gray-500`).

| Family | Utilities |
|---|---|
| Surfaces | `bg-background`, `bg-card`, `bg-popover`, `bg-muted`, `bg-secondary`, `bg-accent`, `bg-sidebar` |
| Text | `text-foreground`, `text-muted-foreground`, `text-card-foreground`, `text-primary`, `text-accent-foreground`, `text-destructive`, `text-success` |
| Brand | `bg-primary` / `text-primary-foreground` (primary = **kin**, the brand green), `bg-kin`, `text-kin`, `fill-kin`, `bg-ember` / `text-on-ember` (**ember**, the companion's marigold accent) |
| Status | `bg-destructive/10 text-destructive`, `text-success`, `bg-primary/10` (opacity steps 5-90 exist for the main tokens) |
| Lines | `border-border`, `border-input`, `ring-ring`, `ring-foreground/10` |
| Charts | `var(--chart-1)` ... `var(--chart-5)` (kin, ember, blue, sage, plum) |
| Avatars | `bg-avatar-blue`, `-violet`, `-green`, `-amber`, `-red`, `-gray` with `text-avatar-foreground` |
| Radius | `rounded-sm` ... `rounded-4xl` scale off `--radius` (0.625rem); cards use `rounded-xl`, buttons `rounded-lg` |
| Type | `font-heading`, `font-sans`, `font-mono`; sizes `text-xs`, `text-sm` (the UI default), `text-base`, `text-lg`, `text-2xl` |

To explore a new variant, compose the existing component and pass token utilities through `className`; classes merge, so yours win over the defaults (`<Button className="bg-ember text-on-ember hover:bg-ember/80">`). Size variants keyed off the parent (e.g. `CardTitle` inside `<Card size="sm">` stays `text-sm`) still apply: put large display text in its own element.

## Where the truth lives

- `styles.css` and the compiled `_ds_bundle.css`: every token is defined in `:root` and `.dark` there. Read it before inventing a color.
- `components/general/<Name>/<Name>.prompt.md` for usage examples and `<Name>.d.ts` for props. Variants are props (`variant`, `size`, `align`), listed in each `.d.ts`.
- Compound components are flat exports: `Card` + `CardHeader`, `CardTitle`, `CardDescription`, `CardAction`, `CardContent`, `CardFooter`; `Dialog` + `DialogContent`, `DialogHeader`, `DialogTitle`, ...; `Message` + `MessageContent` + `Bubble` + `BubbleContent` for chat.

## Charts

Wrap recharts in `ChartContainer` with a `config` mapping each series to a label and a chart token, and draw with the bundle's recharts, `window.Kinby.Recharts` (never a separate recharts import: it breaks the container's sizing). Series colors resolve as `var(--color-<key>)`; use `ChartTooltip` / `ChartLegend` with `ChartTooltipContent` / `ChartLegendContent`.

```jsx
const { ChartContainer, Recharts } = window.Kinby;
const { BarChart, Bar, XAxis, CartesianGrid } = Recharts;
const config = { cost: { label: "API cost", color: "var(--chart-1)" } };

<ChartContainer config={config} className="aspect-auto h-48 w-full">
  <BarChart data={days}>
    <CartesianGrid vertical={false} />
    <XAxis dataKey="day" tickLine={false} axisLine={false} />
    <Bar dataKey="cost" fill="var(--color-cost)" radius={4} />
  </BarChart>
</ChartContainer>
```

## Example

```jsx
const { Card, CardHeader, CardTitle, CardDescription, CardAction, CardContent, CardFooter, Button, Badge } = window.Kinby;

<Card className="w-80">
  <CardHeader>
    <CardTitle>Morning brief</CardTitle>
    <CardDescription>Runs every weekday at 8:00</CardDescription>
    <CardAction><Badge variant="secondary">Routine</Badge></CardAction>
  </CardHeader>
  <CardContent>
    <p className="text-muted-foreground">Reads your calendar and inbox, then writes a plan for the day.</p>
  </CardContent>
  <CardFooter className="justify-between">
    <span className="text-xs text-muted-foreground">Last run 2 hours ago</span>
    <Button size="sm">Run now</Button>
  </CardFooter>
</Card>
```

The shipped stylesheet is compiled ahead of time. It carries every token utility above plus Tailwind's standard scales (`w-*`/`h-*`/`size-*` 0-96, `p-*`/`m-*`/`gap-*` 0-24, `max-w-xs`...`7xl`, `grid-cols-1`...`12`, `md:`/`lg:` for flex/grid/hidden). Arbitrary values (`w-[37rem]`) and other variants may not exist: use an inline `style` for one-off sizes.
