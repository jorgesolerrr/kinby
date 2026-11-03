import type { PickState } from "@kinby/contract"
import { useId } from "react"

import { InstanceAvatar } from "@/components/instance-avatar"
import { AvatarGroup } from "@/components/ui/avatar"
import { Button } from "@/components/ui/button"
import {
  Item,
  ItemActions,
  ItemContent,
  ItemDescription,
  ItemGroup,
  ItemMedia,
  ItemTitle,
} from "@/components/ui/item"
import { instanceName } from "@/lib/instances"
import type { InstancePick } from "@/lib/picks"
import { openConfig } from "@/lib/selection"

const STATES: Record<PickState, string> = {
  "up-to-date": "Up to date",
  behind: "Behind the library",
  edited: "Edited in the instance",
}

/** A row's avatars of the instances that picked its entry, or that none did. */
export function PickedAvatars({ picks }: { picks: InstancePick[] }) {
  if (picks.length === 0) return <span className="text-xs text-muted-foreground">Not picked</span>
  return (
    <AvatarGroup>
      {picks.map(({ instance }) => (
        <InstanceAvatar
          key={instance.instance_id}
          avatar={instance.avatar}
          name={instanceName(instance)}
          label={instanceName(instance)}
          size="sm"
        />
      ))}
    </AvatarGroup>
  )
}

/** Each instance that picked the entry, how its copy stands, and Open to its Config. */
export function UsedBy({ name, picks }: { name: string; picks: InstancePick[] }) {
  const titleId = useId()
  return (
    <section aria-labelledby={titleId} className="flex flex-col gap-2">
      <h3 id={titleId} className="font-medium">
        Used by
      </h3>
      {picks.length === 0 ? (
        <p className="text-muted-foreground">
          No instance picked {name}. Pick it in an instance&apos;s Config.
        </p>
      ) : (
        <ItemGroup aria-label="Used by">
          {picks.map(({ instance, state }) => (
            <Item
              key={instance.instance_id}
              render={<li />}
              aria-label={instanceName(instance)}
              variant="outline"
              size="xs"
            >
              <ItemMedia>
                <InstanceAvatar avatar={instance.avatar} name={instanceName(instance)} size="sm" />
              </ItemMedia>
              <ItemContent>
                <ItemTitle>{instanceName(instance)}</ItemTitle>
                <ItemDescription>{STATES[state]}</ItemDescription>
              </ItemContent>
              <ItemActions>
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => openConfig(instance.instance_id)}
                >
                  Open
                </Button>
              </ItemActions>
            </Item>
          ))}
        </ItemGroup>
      )}
    </section>
  )
}
