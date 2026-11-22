import { cn } from "cn"
import type * as React from "react"

const GUTTERS = "px-4 md:px-8"

/** The centred column a page's header, its tab strip and its open tab share. */
export function PageColumn({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="page-column"
      className={cn("mx-auto w-full max-w-page", GUTTERS, className)}
      {...props}
    />
  )
}

/**
 * The page column's gutters at the full width of the screen, for a view's frame: the base
 * stylesheet centres the page in it, at the column's width, unless the page asks for the full width.
 */
export function PageGutters({ className, ...props }: React.ComponentProps<"div">) {
  return <div data-slot="page-gutters" className={cn(GUTTERS, className)} {...props} />
}
