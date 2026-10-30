// PROTOTYPE: a static mock thread for /proto/thread. Shapes are loose stand-ins for the
// projection in lib/timeline.ts, enough to draw every kind of item. Throwaway.

/** Which state the last turn is in, picked from the prototype bar (?state=). */
export type LastTurn = "done" | "streaming" | "approval" | "takeover" | "failed" | "new"

export const LAST_TURN: { key: LastTurn; label: string }[] = [
  { key: "done", label: "Done" },
  { key: "streaming", label: "Streaming" },
  { key: "approval", label: "Needs approval" },
  { key: "takeover", label: "Needs takeover" },
  { key: "failed", label: "Failed" },
  { key: "new", label: "New thread" },
]

export interface Tool {
  id: string
  name: string
  /** The one argument worth a line: a query, a URL, a path. */
  arg: string
  ms?: number
  error?: string
  running?: boolean
  /** Waiting on the user's approval at the gate. */
  parked?: boolean
  gate?: "approved by you" | "denied by policy"
  input?: string
  output?: string
}

export type Step =
  | { kind: "text"; text: string }
  | { kind: "tools"; tools: Tool[] }
  | { kind: "view"; title: string; change: "Created" | "Changed" }
  | { kind: "notification"; title: string; text: string; delivered: string }

export interface Attachment {
  name: string
  meta: string
  image?: boolean
}

export interface Turn {
  id: string
  /** Where the turn came from. A user turn carries the message that started it. */
  by: { kind: "user"; text: string; attachments?: Attachment[] } | { kind: "routine"; name: string }
  at: string
  /** A new day, or a new session of the agent loop, starts before this turn. */
  session?: { day: string; time: string }
  steps: Step[]
  end?:
    | { kind: "done"; steps: number; tokens: string; took: string }
    | { kind: "failed"; message: string; code: string }
    | { kind: "stopped" }
  recapped?: boolean
  approval?: { tool: string; rule: string; input: string; preview: string }
  takeover?: { reason: string; site: string }
}

const LONG_REPLY = `I found three ways to do the week of **9–13 March** under 900 €. All three avoid flights before 9:00.

## The options

| | Flights | Stay | Total |
|---|---|---|---|
| **A · TAP + Alfama** | TAP 10:40 out, 19:55 back | Apartment in Alfama, 4 nights | **812 €** |
| B · Iberia + Baixa | Iberia 09:15 out, 21:30 back | Hotel Lisboa Baixa, 4 nights | 868 € |
| C · easyJet + Graça | easyJet 13:05 out, 17:40 back | Guesthouse in Graça, 4 nights | 694 € |

## What I'd pick

**Option A.** It's 56 € more than C, but the Friday flight back leaves late enough for a full last day, and the Alfama apartment is refundable until Thursday 20 February. C saves money and loses both half days to the flights.

A few things I checked:

- Your calendar is free from Monday 9 to Friday 13, apart from the call with Marta on Wednesday at 16:00. The apartment has a desk and fibre, so that works.
- You told me last year you'd rather walk than take taxis. Alfama and Baixa are both flat walks to the river; Graça is up the hill.
- TAP's basic fare has no checked bag. With one, A goes to 862 €, still under budget.

\`\`\`text
Mon  9  10:40  MAD → LIS  TP1027
Fri 13  19:55  LIS → MAD  TP1028
\`\`\`

Want me to book A and hold the apartment?`

/** The turns before the last one: the same in every state. */
export const HISTORY: Turn[] = [
  {
    id: "t1",
    by: {
      kind: "user",
      text: "Plan a trip to Lisbon the week of 9 March. Keep it under 900 euros and no flights before 9am.",
      attachments: [{ name: "march-calendar.png", meta: "PNG · 240 KB", image: true }],
    },
    at: "22:04",
    session: { day: "Yesterday", time: "22:04" },
    steps: [
      { kind: "text", text: "I'll check flights and places to stay for that week." },
      {
        kind: "tools",
        tools: [
          {
            id: "a1",
            name: "memory.recall",
            arg: "Jorge travel preferences",
            ms: 180,
            input: '{ "query": "Jorge travel preferences" }',
            output: "3 facts: prefers walking; window seat; no early flights",
          },
          {
            id: "a2",
            name: "web_search",
            arg: "flights Madrid Lisbon 9 March",
            ms: 1240,
            input: '{ "query": "flights Madrid Lisbon 9 March" }',
            output: "10 results · skyscanner.es, flytap.com, iberia.com …",
          },
          {
            id: "a3",
            name: "browser.open",
            arg: "flytap.com",
            ms: 4310,
            output: "Page loaded · 3 fares found",
          },
          {
            id: "a4",
            name: "browser.open",
            arg: "booking.com/lisbon",
            ms: 5120,
            output: "Page loaded · 41 stays, filtered to 6",
          },
          {
            id: "a5",
            name: "calendar.list",
            arg: "9–13 March",
            ms: 210,
            output: "1 event: Call with Marta, Wed 16:00",
          },
        ],
      },
      { kind: "text", text: LONG_REPLY },
      { kind: "view", title: "Trips", change: "Changed" },
    ],
    end: { kind: "done", steps: 7, tokens: "18.4k", took: "48s" },
    recapped: true,
  },
  {
    id: "t2",
    by: { kind: "user", text: "Book A, with one checked bag, and hold the apartment." },
    at: "09:12",
    session: { day: "Today", time: "09:12" },
    steps: [
      {
        kind: "tools",
        tools: [
          {
            id: "b1",
            name: "browser.fill",
            arg: "flytap.com checkout",
            ms: 8020,
            output: "Passenger details filled from the vault",
          },
          {
            id: "b2",
            name: "payments.charge",
            arg: "862.00 EUR · Visa ·· 4417",
            ms: 2300,
            gate: "approved by you",
            output: "Charged · booking ref TP-7QX2LM",
          },
          {
            id: "b3",
            name: "browser.click",
            arg: "booking.com · Reserve",
            ms: 30000,
            error: "timed out after 30 s",
          },
          {
            id: "b4",
            name: "browser.click",
            arg: "booking.com · Reserve",
            ms: 3900,
            output: "Reserved · free cancellation until 20 Feb",
          },
        ],
      },
      {
        kind: "text",
        text: "Done. The flights are booked (ref **TP-7QX2LM**, 862 € with the bag) and the Alfama apartment is held until Thursday 20 February. booking.com timed out once, so I tried again.",
      },
      {
        kind: "notification",
        title: "Lisbon trip booked",
        text: "Flights TP-7QX2LM, Alfama apartment held until 20 Feb.",
        delivered: "Delivered on Telegram",
      },
    ],
    end: { kind: "done", steps: 6, tokens: "9.1k", took: "1m 12s" },
  },
  {
    id: "t3",
    by: { kind: "routine", name: "Morning brief" },
    at: "09:30",
    steps: [
      { kind: "tools", tools: [{ id: "c1", name: "weather", arg: "Lisbon, 9–13 March", ms: 400 }] },
      {
        kind: "text",
        text: "Forecast for your trip: 17–19 °C, rain likely on Tuesday. Pack a light jacket.",
      },
    ],
    end: { kind: "done", steps: 2, tokens: "1.2k", took: "6s" },
  },
]

const ASK = "Also find a restaurant for Friday night near the apartment, and book it for two."

const SEARCH: Tool[] = [
  {
    id: "d1",
    name: "web_search",
    arg: "restaurants Alfama Friday dinner",
    ms: 980,
    output: "10 results · timeout.com, thefork.pt …",
  },
  {
    id: "d2",
    name: "browser.open",
    arg: "thefork.pt/alfama",
    ms: 3700,
    output: "Page loaded · 12 restaurants with tables Friday 21:00",
  },
]

const PICK =
  "**Taberna da Rua das Flores** has a table for two at 21:00 on Friday, a 6-minute walk from the apartment. Petiscos, around 30 € a head, and it's the one Ana mentioned last month."

/** The last turn, in the state the prototype bar picked. */
export function lastTurn(state: LastTurn, shown: number): Turn | undefined {
  const base = { id: "t4", by: { kind: "user", text: ASK } as const, at: "10:02" }
  switch (state) {
    case "new":
      return undefined
    case "done":
      return {
        ...base,
        steps: [
          { kind: "tools", tools: SEARCH },
          { kind: "text", text: PICK },
          {
            kind: "tools",
            tools: [
              {
                id: "d3",
                name: "email.send",
                arg: "reservas@tabernaflores.pt",
                ms: 900,
                gate: "approved by you",
              },
            ],
          },
          { kind: "text", text: "Booked for 21:00 on Friday 13 March, under your name." },
        ],
        end: { kind: "done", steps: 5, tokens: "6.3k", took: "38s" },
      }
    case "approval":
      return {
        ...base,
        steps: [
          { kind: "tools", tools: SEARCH },
          { kind: "text", text: PICK },
          {
            kind: "tools",
            tools: [
              { id: "d3", name: "email.send", arg: "reservas@tabernaflores.pt", parked: true },
            ],
          },
        ],
        approval: {
          tool: "email.send",
          rule: "email.send asks in ask mode",
          input: "to: reservas@tabernaflores.pt\nsubject: Reserva para dos, viernes 13 de marzo",
          preview:
            "Hola, quería reservar una mesa para dos personas el viernes 13 de marzo a las 21:00, a nombre de Jorge Soler. Gracias.",
        },
      }
    case "takeover":
      return {
        ...base,
        steps: [
          { kind: "tools", tools: SEARCH },
          { kind: "text", text: PICK },
          {
            kind: "tools",
            tools: [{ id: "d3", name: "browser.click", arg: "thefork.pt · Book", running: true }],
          },
        ],
        takeover: {
          reason: "TheFork wants you to sign in before it takes the booking.",
          site: "thefork.pt",
        },
      }
    case "failed":
      return {
        ...base,
        steps: [{ kind: "tools", tools: SEARCH }],
        end: {
          kind: "failed",
          message: "The model provider is overloaded.",
          code: "PROVIDER_OVERLOADED",
        },
      }
    case "streaming":
      return streaming(base, shown)
  }
}

/** The streaming turn, `shown` ticks in: tools start and finish, then the reply types itself. */
function streaming(base: Pick<Turn, "id" | "by" | "at">, shown: number): Turn {
  const steps: Step[] = []
  if (shown >= 1) {
    const tools = SEARCH.slice(0, shown >= 3 ? 2 : 1).map((t, i) => ({
      ...t,
      running: (shown === 1 && i === 0) || (shown === 3 && i === 1),
      ms: (shown === 1 && i === 0) || (shown === 3 && i === 1) ? undefined : t.ms,
    }))
    steps.push({ kind: "tools", tools })
  }
  if (shown >= 4) {
    const words = PICK.split(" ")
    steps.push({ kind: "text", text: words.slice(0, (shown - 3) * 4).join(" ") })
  }
  return { ...base, steps }
}

/** How many ticks the streaming turn takes to finish typing. */
export const STREAM_TICKS = 3 + Math.ceil(PICK.split(" ").length / 4)

/** A notice the hub reports about the instance, shown while it holds. */
export const NOTICE = {
  title: "Kin's Personal browser profile is signed out of booking.com",
  action: "Sign in",
}

export const MODES = [
  { value: "read-only", label: "Read-only" },
  { value: "ask", label: "Ask" },
  { value: "auto", label: "Auto" },
  { value: "full-access", label: "Full access" },
] as const
