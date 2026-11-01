// PROTOTYPE: the Trips view as Kin keeps it, built from the real view catalogue. Throwaway.
import type { View } from "@kinby/contract"

export const TRIPS: View = {
  id: "trips",
  title: "Trips",
  updated_at: new Date(Date.now() - 2 * 60_000).toISOString(),
  data: {
    trip: "Lisbon · 9–13 March",
    budget: "€900",
    spent: "€612",
    nights: "4",
    stays: [
      { name: "Alfama apartment", price: "€420", note: "Refundable until Thu", tag: "Held" },
      { name: "Hotel Baixa House", price: "€510", note: "Breakfast included", tag: "Option" },
      { name: "LX Factory loft", price: "€380", note: "20 min from centre", tag: "Option" },
    ],
    todo: [
      { label: "Book the 10:40 TAP flight", done: true },
      { label: "Confirm the apartment by Thursday", done: false },
      { label: "Dinner on Friday at Taberna", done: false },
    ],
  },
  spec: {
    components: [
      {
        id: "root",
        component: "Card",
        title: { path: "/trip" },
        children: ["stats", "table", "list"],
      },
      { id: "stats", component: "Row", children: ["s-budget", "s-spent", "s-nights"] },
      { id: "s-budget", component: "Stat", label: "Budget", value: { path: "/budget" } },
      { id: "s-spent", component: "Stat", label: "Booked so far", value: { path: "/spent" } },
      { id: "s-nights", component: "Stat", label: "Nights", value: { path: "/nights" } },
      {
        id: "table",
        component: "Table",
        rows: { path: "/stays" },
        columns: [
          { header: "Stay", cell: "c-name" },
          { header: "Price", cell: "c-price" },
          { header: "Note", cell: "c-note" },
          { header: "", cell: "c-tag" },
        ],
      },
      { id: "c-name", component: "Text", text: { path: "/name" } },
      { id: "c-price", component: "Text", text: { path: "/price" } },
      { id: "c-note", component: "Text", text: { path: "/note" } },
      { id: "c-tag", component: "Badge", label: { path: "/tag" } },
      {
        id: "list",
        component: "Checklist",
        items: { path: "/todo" },
        label: { path: "/label" },
        done: { path: "/done" },
      },
    ],
  },
}
