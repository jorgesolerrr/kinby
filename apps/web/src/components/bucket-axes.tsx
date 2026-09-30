import { CartesianGrid, XAxis, YAxis } from "recharts"

import { dayLabel } from "@/lib/stats"

/** The grid and axes of a chart over a range's buckets, each named by the UTC day it starts on. */
export function BucketAxes({
  allowDecimals = false,
  tickFormatter,
}: {
  allowDecimals?: boolean
  tickFormatter?: (value: number) => string
}) {
  return (
    <>
      <CartesianGrid vertical={false} />
      <XAxis
        dataKey="start"
        tickLine={false}
        axisLine={false}
        minTickGap={16}
        tickFormatter={dayLabel}
      />
      <YAxis
        tickLine={false}
        axisLine={false}
        width={40}
        allowDecimals={allowDecimals}
        tickFormatter={tickFormatter}
      />
    </>
  )
}
