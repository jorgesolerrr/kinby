import {
  Bubble,
  BubbleContent,
  Message,
  MessageContent,
  MessageFooter,
  MessageHeader,
} from "@kinby/web"

export const Conversation = () => (
  <div className="flex w-[28rem] flex-col gap-3">
    <Message align="end">
      <MessageContent>
        <Bubble variant="secondary" align="end">
          <BubbleContent>Can you move my 3pm with Ana to tomorrow morning?</BubbleContent>
        </Bubble>
      </MessageContent>
    </Message>
    <Message>
      <MessageContent>
        <MessageHeader className="px-0">kinby</MessageHeader>
        <Bubble variant="ghost">
          <BubbleContent>
            Done. I moved it to 9:30 tomorrow and sent Ana a note with the new time. Your
            afternoon is now free from 2pm.
          </BubbleContent>
        </Bubble>
      </MessageContent>
    </Message>
  </div>
)

export const WithFooter = () => (
  <div className="w-[28rem]">
    <Message>
      <MessageContent>
        <MessageHeader>kinby</MessageHeader>
        <Bubble variant="muted">
          <BubbleContent>I found three invoices due this week. Want me to pay them?</BubbleContent>
        </Bubble>
        <MessageFooter>Sent 10:42</MessageFooter>
      </MessageContent>
    </Message>
  </div>
)
