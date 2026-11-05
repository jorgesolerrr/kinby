import { cn } from "cn"
import type * as React from "react"

/** The centred column a page's header, its tab strip and its open tab share. */
export function PageColumn({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="page-column"
      className={cn("mx-auto w-full max-w-4xl px-4 md:px-8", className)}
      {...props}
    />
  )
}
