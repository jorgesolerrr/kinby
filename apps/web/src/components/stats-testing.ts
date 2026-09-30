import { act, fireEvent, within } from "@testing-library/react"

/**
 * What the chart in `region` shows for each bucket, first to last, as a keyboard reader steps
 * through it: focusing the chart opens the first bucket's tooltip and ArrowRight moves along.
 */
export function bucketTooltips(region: HTMLElement, count: number): string[] {
  const chart = region.querySelector("svg.recharts-surface")
  if (!chart) throw new Error("The region has no chart")
  const read = () => region.querySelector(".recharts-tooltip-wrapper")?.textContent ?? ""
  act(() => {
    fireEvent.focus(chart)
  })
  const shown = [read()]
  while (shown.length < count) {
    act(() => {
      fireEvent.keyDown(chart, { key: "ArrowRight" })
    })
    shown.push(read())
  }
  return shown
}

/** What the legend of the chart in `region` reads, as one string. */
export function legend(region: HTMLElement): string {
  return region.querySelector(".recharts-legend-wrapper")?.textContent ?? ""
}

/** Each row of `table` below its header, as the text of its cells. */
export function rows(table: HTMLElement): (string | null)[][] {
  return within(table)
    .getAllByRole("row")
    .slice(1)
    .map((row) =>
      within(row)
        .getAllByRole("cell")
        .map((cell) => cell.textContent),
    )
}
