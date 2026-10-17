import {
  Badge,
  Button,
  Item,
  ItemActions,
  ItemContent,
  ItemDescription,
  ItemFooter,
  ItemGroup,
  ItemHeader,
  ItemMedia,
  ItemSeparator,
  ItemTitle,
} from "@kinby/web"
import { BrainIcon, ChevronRightIcon, KeyRoundIcon, PlusIcon, RepeatIcon } from "lucide-react"

export const Variants = () => (
  <div className="flex w-[28rem] flex-col gap-3">
    <Item>
      <ItemContent>
        <ItemTitle>inbox-triage</ItemTitle>
        <ItemDescription>Sorts new mail into reply, read later and archive.</ItemDescription>
      </ItemContent>
    </Item>
    <Item variant="outline">
      <ItemContent>
        <ItemTitle>calendar-planner</ItemTitle>
        <ItemDescription>Finds free slots and proposes meeting times.</ItemDescription>
      </ItemContent>
    </Item>
    <Item variant="muted">
      <ItemContent>
        <ItemTitle>expense-report</ItemTitle>
        <ItemDescription>Collects receipts from the inbox into a monthly sheet.</ItemDescription>
      </ItemContent>
    </Item>
  </div>
)

export const SkillPicks = () => (
  <ItemGroup className="w-[28rem]">
    <Item size="sm" variant="outline">
      <ItemContent>
        <ItemTitle>
          repository-survey
          <Badge variant="secondary">Factory</Badge>
        </ItemTitle>
        <ItemDescription>Picked, installed on the instance</ItemDescription>
      </ItemContent>
      <ItemActions>
        <Button variant="outline" size="sm">
          Drop
        </Button>
      </ItemActions>
    </Item>
    <Item size="sm" variant="outline">
      <ItemContent>
        <ItemTitle>
          web-research
          <Badge variant="secondary">Library</Badge>
        </ItemTitle>
        <ItemDescription>Picked, installs on next start</ItemDescription>
      </ItemContent>
      <ItemActions>
        <Button variant="outline" size="sm">
          Drop
        </Button>
      </ItemActions>
    </Item>
    <Item size="sm">
      <ItemContent>
        <ItemTitle>meeting-notes</ItemTitle>
        <ItemDescription>Turns a call transcript into decisions and follow-ups.</ItemDescription>
      </ItemContent>
      <ItemActions>
        <Button variant="outline" size="sm">
          <PlusIcon data-icon="inline-start" />
          Add
        </Button>
      </ItemActions>
    </Item>
  </ItemGroup>
)

export const WithMedia = () => (
  <ItemGroup className="w-[28rem]">
    <Item variant="outline">
      <ItemMedia variant="icon">
        <KeyRoundIcon />
      </ItemMedia>
      <ItemContent>
        <ItemTitle>GITHUB_TOKEN</ItemTitle>
        <ItemDescription>Set 3 days ago · read by repository-survey</ItemDescription>
      </ItemContent>
      <ItemActions>
        <Button variant="ghost" size="sm">
          Replace
        </Button>
      </ItemActions>
    </Item>
    <ItemSeparator />
    <Item variant="outline">
      <ItemMedia variant="icon">
        <BrainIcon />
      </ItemMedia>
      <ItemContent>
        <ItemTitle>Prefers meetings after 10:00</ItemTitle>
        <ItemDescription>Preference · learned from the thread on Oct 12</ItemDescription>
      </ItemContent>
      <ItemActions>
        <ChevronRightIcon className="size-4 text-muted-foreground" />
      </ItemActions>
    </Item>
  </ItemGroup>
)

export const HeaderFooter = () => (
  <Item variant="outline" className="w-[28rem]">
    <ItemHeader>
      <span className="flex items-center gap-2 font-medium">
        <RepeatIcon className="size-4" />
        Morning brief
      </span>
      <Badge variant="success">Ran 2h ago</Badge>
    </ItemHeader>
    <ItemContent>
      <ItemDescription lines="all">
        Reads your calendar and inbox, then writes a short plan for the day with anything that
        needs a reply before noon.
      </ItemDescription>
    </ItemContent>
    <ItemFooter>
      <span className="text-muted-foreground">Weekdays at 8:00</span>
      <Button size="sm">Run now</Button>
    </ItemFooter>
  </Item>
)

export const Sizes = () => (
  <ItemGroup className="w-[28rem]">
    {(["default", "sm", "xs"] as const).map((size) => (
      <Item key={size} size={size} variant="outline">
        <ItemContent>
          <ItemTitle>Weekly invoices</ItemTitle>
          <ItemDescription>Size {size} · last run Monday</ItemDescription>
        </ItemContent>
      </Item>
    ))}
  </ItemGroup>
)
