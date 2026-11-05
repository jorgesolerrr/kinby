import { Collapsible as CollapsiblePrimitive } from "@base-ui/react/collapsible"
import { cva, type VariantProps } from "class-variance-authority"
import { cn } from "cn"

function Collapsible({ ...props }: CollapsiblePrimitive.Root.Props) {
  return <CollapsiblePrimitive.Root data-slot="collapsible" {...props} />
}

function CollapsibleTrigger({ ...props }: CollapsiblePrimitive.Trigger.Props) {
  return <CollapsiblePrimitive.Trigger data-slot="collapsible-trigger" {...props} />
}

const collapsibleContentVariants = cva("", {
  variants: {
    variant: {
      default: "",
      muted: "border-t bg-muted/40 p-4",
    },
  },
  defaultVariants: {
    variant: "default",
  },
})

function CollapsibleContent({
  className,
  variant = "default",
  ...props
}: CollapsiblePrimitive.Panel.Props & VariantProps<typeof collapsibleContentVariants>) {
  return (
    <CollapsiblePrimitive.Panel
      data-slot="collapsible-content"
      data-variant={variant}
      className={cn(collapsibleContentVariants({ variant }), className)}
      {...props}
    />
  )
}

export { Collapsible, CollapsibleTrigger, CollapsibleContent }
