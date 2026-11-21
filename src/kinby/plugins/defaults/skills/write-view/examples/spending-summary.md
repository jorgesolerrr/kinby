# A summary with a chart

Spending, which a routine refreshes from the bank's export each night. The user comes to it for
one answer, how this month is going, so one figure leads at 2.5rem with a sentence that puts it in
context, and nothing competes with it. The months beside it are a bar chart in which colour marks
only the month in progress. The categories share their fields, so they are a table, with figures
right-aligned in tabular numerals and a thin bar for each share. Only a category over its average
turns `--destructive`. At pane width the figure and the chart stack.

The chart follows the theme as the skill's Charts section shows. A redraw destroys the chart and
draws it again with animation off, so a refresh doesn't flash.

## Page

```html
<style>
  .summary {
    container-type: inline-size;
  }
  .top {
    display: grid;
    gap: 1.5rem;
    margin-bottom: 2rem;
  }
  @container (min-width: 44rem) {
    .top {
      grid-template-columns: 15rem 1fr;
      align-items: end;
    }
  }
  .label {
    margin: 0;
    color: var(--muted-foreground);
    font-size: 0.75rem;
  }
  .figure {
    margin: 0.25rem 0 0.5rem;
    font-size: 2.5rem;
    font-weight: 600;
    line-height: 1;
    font-variant-numeric: tabular-nums;
  }
  .context {
    margin: 0;
    color: var(--muted-foreground);
    font-size: 0.875rem;
  }
  .context strong {
    color: var(--foreground);
    font-weight: 500;
  }
  .chart {
    position: relative;
    height: 11rem;
  }
  h2 {
    margin-bottom: 0.25rem;
  }
  table {
    font-size: 0.875rem;
  }
  th {
    font-size: 0.75rem;
  }
  th,
  td {
    padding: 0.5rem 0.75rem;
  }
  th:first-child,
  td:first-child {
    padding-left: 0;
  }
  th:last-child,
  td:last-child {
    padding-right: 0;
  }
  .number {
    width: 6rem;
    text-align: right;
    font-variant-numeric: tabular-nums;
    white-space: nowrap;
  }
  .average {
    color: var(--muted-foreground);
  }
  .over {
    color: var(--destructive);
  }
  .share {
    width: 30%;
  }
  .bar {
    height: 0.375rem;
    border-radius: 999px;
    background: var(--chart-1);
  }
  .bar.over {
    background: var(--destructive);
  }
  @container (max-width: 34rem) {
    .share {
      display: none;
    }
  }
</style>

<div class="summary">
  <div class="top">
    <div>
      <p class="label" id="label"></p>
      <p class="figure" id="figure"></p>
      <p class="context" id="context"></p>
    </div>
    <div class="chart">
      <canvas id="months" role="img" aria-label="Spending by month"></canvas>
    </div>
  </div>
  <h2>By category</h2>
  <table>
    <thead>
      <tr>
        <th>Category</th>
        <th class="number">This month</th>
        <th class="number">Average</th>
        <th class="share" aria-label="Share of the month"></th>
      </tr>
    </thead>
    <tbody id="categories"></tbody>
  </table>
</div>

<script src="/sandbox/chart.js"></script>
<script>
  const swatch = document.createElement("canvas").getContext("2d", { willReadFrequently: true })
  function token(name) {
    swatch.clearRect(0, 0, 1, 1)
    swatch.fillStyle = getComputedStyle(document.documentElement).getPropertyValue(name)
    swatch.fillRect(0, 0, 1, 1)
    const [red, green, blue] = swatch.getImageData(0, 0, 1, 1).data
    return `rgb(${red}, ${green}, ${blue})`
  }

  function element(tag, className, text) {
    const node = document.createElement(tag)
    if (className) node.className = className
    if (text !== undefined) node.textContent = text
    return node
  }

  function monthName(month, length) {
    return new Date(`${month}-01T00:00`).toLocaleDateString(undefined, { month: length })
  }

  let chart
  function draw(data) {
    const money = new Intl.NumberFormat(undefined, {
      style: "currency",
      currency: data.currency,
      maximumFractionDigits: 0,
    })
    const current = data.months.at(-1)
    const earlier = data.months.slice(0, -1)
    const average = earlier.reduce((sum, month) => sum + month.total, 0) / earlier.length
    const pace = current.total / (data.day / data.days_in_month)
    const difference = Math.round(Math.abs(pace - average) / average * 100)

    document.getElementById("label").textContent = `${monthName(current.month, "long")} so far`
    document.getElementById("figure").textContent = money.format(current.total)
    document.getElementById("context").replaceChildren(
      `On pace for ${money.format(pace)}, `,
      element("strong", "", `${difference}% ${pace > average ? "over" : "under"}`),
      ` your ${earlier.length}-month average.`,
    )

    Chart.defaults.color = token("--muted-foreground")
    Chart.defaults.borderColor = token("--border")
    Chart.defaults.font.family = getComputedStyle(document.body).fontFamily
    chart?.destroy()
    chart = new Chart(document.getElementById("months"), {
      type: "bar",
      data: {
        labels: data.months.map((month) => monthName(month.month, "short")),
        datasets: [
          {
            label: "Spent",
            data: data.months.map((month) => month.total),
            backgroundColor: data.months.map((month) =>
              month === current ? token("--chart-1") : token("--border"),
            ),
            borderRadius: 4,
          },
        ],
      },
      options: {
        animation: false,
        maintainAspectRatio: false,
        plugins: {
          legend: { display: false },
          tooltip: { callbacks: { label: (item) => money.format(item.raw) } },
        },
        scales: {
          x: { grid: { display: false } },
          y: { border: { display: false }, ticks: { maxTicksLimit: 4, callback: money.format } },
        },
      },
    })

    const largest = Math.max(...data.categories.map((category) => category.spent))
    const rows = data.categories
      .toSorted((a, b) => b.spent - a.spent)
      .map((category) => {
        const over = category.spent > category.average * 1.1
        const bar = element("div", over ? "bar over" : "bar")
        bar.style.width = `${(category.spent / largest) * 100}%`
        const share = element("td", "share")
        share.append(bar)
        const tr = element("tr")
        tr.append(
          element("td", "", category.name),
          element("td", over ? "number over" : "number", money.format(category.spent)),
          element("td", "number average", money.format(category.average)),
          share,
        )
        return tr
      })
    document.getElementById("categories").replaceChildren(...rows)
  }

  draw(kinby.data)
  kinby.onData(draw)
  new MutationObserver(() => draw(kinby.data)).observe(document.documentElement, {
    attributeFilter: ["class"],
  })
</script>
```

## Data

```json
{
  "currency": "EUR",
  "day": 21,
  "days_in_month": 30,
  "months": [
    { "month": "2026-06", "total": 2486 },
    { "month": "2026-07", "total": 3120 },
    { "month": "2026-08", "total": 2710 },
    { "month": "2026-09", "total": 2395 },
    { "month": "2026-10", "total": 2642 },
    { "month": "2026-11", "total": 1712 }
  ],
  "categories": [
    { "name": "Rent", "spent": 950, "average": 950 },
    { "name": "Groceries", "spent": 286, "average": 412 },
    { "name": "Eating out", "spent": 214, "average": 168 },
    { "name": "Transport", "spent": 96, "average": 120 },
    { "name": "Subscriptions", "spent": 64, "average": 64 },
    { "name": "Other", "spent": 102, "average": 230 }
  ]
}
```
