import type { ComponentProps } from "react"

/** kinby's mark: a stem (you) and a disc at its side (kinby). Its colors follow the theme. */
export function KinbyMark(props: ComponentProps<"svg">) {
  return (
    <svg viewBox="0 0 48 48" aria-hidden="true" {...props}>
      <rect x="8" y="6" width="12" height="36" rx="6" className="fill-kin" />
      <circle cx="32" cy="34" r="8" className="fill-ember" />
    </svg>
  )
}
