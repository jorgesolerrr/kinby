import {
  Button,
  Empty,
  EmptyContent,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@kinby/web"
import { BotIcon, CirclePauseIcon, MessagesSquareIcon, PlayIcon, PlusIcon } from "lucide-react"

export const Default = () => (
  <Empty className="w-[28rem] border">
    <EmptyHeader>
      <EmptyMedia variant="icon">
        <BotIcon />
      </EmptyMedia>
      <EmptyTitle>Nothing here yet</EmptyTitle>
      <EmptyDescription>What Research does will show here.</EmptyDescription>
    </EmptyHeader>
  </Empty>
)

export const WithAction = () => (
  <Empty className="w-[28rem] border">
    <EmptyHeader>
      <EmptyMedia variant="icon">
        <CirclePauseIcon />
      </EmptyMedia>
      <EmptyTitle>Research is stopped</EmptyTitle>
      <EmptyDescription>It does nothing until it starts.</EmptyDescription>
    </EmptyHeader>
    <EmptyContent>
      <Button>
        <PlayIcon data-icon="inline-start" />
        Start
      </Button>
    </EmptyContent>
  </Empty>
)

export const NoThreads = () => (
  <Empty className="w-[28rem] border">
    <EmptyHeader>
      <EmptyMedia variant="icon">
        <MessagesSquareIcon />
      </EmptyMedia>
      <EmptyTitle>No threads yet</EmptyTitle>
      <EmptyDescription>
        Ask kinby something to start one. Routines that run on their own land here too.
      </EmptyDescription>
    </EmptyHeader>
    <EmptyContent>
      <Button variant="outline">
        <PlusIcon data-icon="inline-start" />
        New thread
      </Button>
    </EmptyContent>
  </Empty>
)

export const TextOnly = () => (
  <Empty className="w-[28rem] border">
    <EmptyHeader>
      <EmptyTitle>Nothing matches</EmptyTitle>
      <EmptyDescription>Search matches descriptions and subjects.</EmptyDescription>
    </EmptyHeader>
  </Empty>
)
