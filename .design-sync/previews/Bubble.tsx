import {
  Bubble,
  BubbleContent,
  BubbleGroup,
  BubbleReactions,
  Message,
  MessageContent,
} from "@kinby/web"

const VARIANTS = [
  ["default", "Your morning brief is ready."],
  ["secondary", "Move my 3pm with Ana to tomorrow."],
  ["muted", "Three invoices are due this week."],
  ["tinted", "Saved to memory: you prefer aisle seats."],
  ["outline", "Weekly review runs Friday at 17:00."],
  ["ghost", "Done. Your afternoon is free from 2pm."],
  ["destructive", "The GITHUB_TOKEN secret expired, so the sync failed."],
] as const

export const Variants = () => (
  <div className="flex flex-col gap-3" style={{ width: 460 }}>
    {VARIANTS.map(([variant, text]) => (
      <div key={variant} className="flex items-center gap-3">
        <span className="shrink-0 font-mono text-xs text-muted-foreground" style={{ width: 88 }}>
          {variant}
        </span>
        <Bubble variant={variant}>
          <BubbleContent>{text}</BubbleContent>
        </Bubble>
      </div>
    ))}
  </div>
)

export const Exchange = () => (
  <div className="flex flex-col gap-3" style={{ width: 448 }}>
    <Message align="end">
      <MessageContent>
        <Bubble variant="secondary" align="end">
          <BubbleContent>What did I spend on the API this week?</BubbleContent>
        </Bubble>
      </MessageContent>
    </Message>
    <Message>
      <MessageContent>
        <BubbleGroup>
          <Bubble variant="muted">
            <BubbleContent>$12.40 across 86 turns, mostly in the coder instance.</BubbleContent>
          </Bubble>
          <Bubble variant="muted">
            <BubbleContent>That is about a third of last week.</BubbleContent>
          </Bubble>
        </BubbleGroup>
      </MessageContent>
    </Message>
  </div>
)

export const WithReactions = () => (
  <div className="flex flex-col gap-6 pb-2" style={{ width: 448 }}>
    <Bubble variant="tinted">
      <BubbleContent>I booked the 8:05 train to Valencia and added it to your calendar.</BubbleContent>
      <BubbleReactions>
        <span>👍</span>
      </BubbleReactions>
    </Bubble>
    <Bubble variant="secondary" align="end">
      <BubbleContent>Perfect, thanks.</BubbleContent>
      <BubbleReactions align="start">
        <span>❤️</span>
      </BubbleReactions>
    </Bubble>
  </div>
)
