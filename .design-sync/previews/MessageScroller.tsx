import {
  Bubble,
  BubbleContent,
  Message,
  MessageContent,
  MessageHeader,
  MessageScroller,
  MessageScrollerButton,
  MessageScrollerContent,
  MessageScrollerItem,
  MessageScrollerProvider,
  MessageScrollerViewport,
} from "@kinby/web"
import type { ReactNode } from "react"

const TURNS: [string, string][] = [
  [
    "Can you check whether the nightly backup ran?",
    "It did. The backup finished at 03:12 and wrote 1.4 GB to the NAS.",
  ],
  [
    "Remind me to renew the domain next week.",
    "Added a routine for Monday at 9:00 that reminds you to renew kinby.dev.",
  ],
  [
    "What's on my calendar tomorrow?",
    "Standup at 9:30, lunch with Ana at 13:00, and the dentist at 17:15.",
  ],
  [
    "Move the dentist to Thursday.",
    "Done. The clinic confirmed Thursday at 17:15 and I updated your calendar.",
  ],
  [
    "Summarize the open issues on the hub repo.",
    "There are 7 open issues: 3 bugs in the thread panel, 2 specs, and 2 waiting on you.",
  ],
]

function Turns({ turns }: { turns: [string, string][] }) {
  return turns.map(([request, reply], index) => (
    <MessageScrollerItem key={request} messageId={`turn-${index}`} scrollAnchor>
      <div className="flex flex-col gap-3">
        <Message align="end">
          <MessageContent>
            <Bubble variant="secondary" align="end">
              <BubbleContent>{request}</BubbleContent>
            </Bubble>
          </MessageContent>
        </Message>
        <Message>
          <MessageContent>
            <MessageHeader className="px-0">kinby</MessageHeader>
            <Bubble variant="ghost">
              <BubbleContent>{reply}</BubbleContent>
            </Bubble>
          </MessageContent>
        </Message>
      </div>
    </MessageScrollerItem>
  ))
}

function Frame({ height, children }: { height: number; children: ReactNode }) {
  return (
    <div className="flex flex-col rounded-xl border bg-background" style={{ width: 480, height }}>
      {children}
    </div>
  )
}

export const Thread = () => (
  <Frame height={360}>
    <MessageScrollerProvider autoScroll defaultScrollPosition="end" scrollPreviousItemPeek={0}>
      <MessageScroller className="flex-1">
        <MessageScrollerViewport>
          <MessageScrollerContent className="p-4">
            <Turns turns={TURNS} />
          </MessageScrollerContent>
        </MessageScrollerViewport>
        <MessageScrollerButton />
      </MessageScroller>
    </MessageScrollerProvider>
  </Frame>
)

export const ScrolledToStart = () => (
  <Frame height={360}>
    <MessageScrollerProvider defaultScrollPosition="start">
      <MessageScroller className="flex-1">
        <MessageScrollerViewport>
          <MessageScrollerContent className="p-4">
            <Turns turns={TURNS} />
          </MessageScrollerContent>
        </MessageScrollerViewport>
        <MessageScrollerButton />
      </MessageScroller>
    </MessageScrollerProvider>
  </Frame>
)

export const ShortThread = () => (
  <Frame height={220}>
    <MessageScrollerProvider defaultScrollPosition="last-anchor">
      <MessageScroller className="flex-1">
        <MessageScrollerViewport>
          <MessageScrollerContent className="p-4">
            <Turns turns={TURNS.slice(0, 1)} />
          </MessageScrollerContent>
        </MessageScrollerViewport>
        <MessageScrollerButton />
      </MessageScroller>
    </MessageScrollerProvider>
  </Frame>
)
