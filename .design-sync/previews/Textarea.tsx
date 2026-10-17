import { Textarea } from "@kinby/web"

export const Default = () => (
  <div className="w-80">
    <Textarea placeholder="Tell kinby what this skill does and when to use it" />
  </div>
)

export const Filled = () => (
  <div className="w-80">
    <Textarea
      className="min-h-32"
      defaultValue={
        "Every weekday at 8:00, read my calendar and inbox.\nWrite a short plan for the day and flag anything that needs a reply before noon."
      }
    />
  </div>
)

export const Invalid = () => (
  <div className="w-80">
    <Textarea aria-invalid defaultValue="Summarize" />
  </div>
)

export const Disabled = () => (
  <div className="w-80">
    <Textarea disabled defaultValue="This routine is managed by the hub and cannot be edited here." />
  </div>
)
