import type { Avatar as InstanceAvatarChoice } from "@kinby/contract"
import type * as React from "react"

import { Avatar, AvatarFallback } from "@/components/ui/avatar"

/** An instance's avatar: the first letter of its name, in the shape and color it was given. */
export function InstanceAvatar({
  avatar,
  name,
  size,
  label,
}: {
  avatar: InstanceAvatarChoice
  name: string
  size?: React.ComponentProps<typeof Avatar>["size"]
  /** What a screen reader says for it. Without one it is hidden, as the name is said beside it. */
  label?: string
}) {
  const said = label === undefined ? { "aria-hidden": true } : { role: "img", "aria-label": label }
  return (
    <Avatar shape={avatar.shape} size={size} {...said}>
      <AvatarFallback variant={avatar.color}>
        {name.trim().charAt(0).toUpperCase() || "?"}
      </AvatarFallback>
    </Avatar>
  )
}
