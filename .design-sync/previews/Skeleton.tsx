import { Skeleton } from "@kinby/web"

export const ThreadList = () => (
  <div className="flex w-80 flex-col gap-4">
    {[0, 1, 2].map((row) => (
      <div key={row} className="flex items-center gap-3">
        <Skeleton className="size-8 rounded-full" />
        <div className="flex flex-1 flex-col gap-2">
          <Skeleton className="h-4 w-3/4" />
          <Skeleton className="h-3 w-1/2" />
        </div>
      </div>
    ))}
  </div>
)

export const Card = () => (
  <div className="flex w-80 flex-col gap-3 rounded-xl border bg-card p-4">
    <Skeleton className="h-5 w-1/2" />
    <Skeleton className="h-3 w-1/3" />
    <Skeleton className="h-24 w-full" />
    <div className="flex justify-end gap-2">
      <Skeleton className="h-8" style={{ width: "5rem" }} />
      <Skeleton className="h-8" style={{ width: "5rem" }} />
    </div>
  </div>
)

export const Panel = () => (
  <div className="flex w-[28rem] flex-col gap-2">
    <Skeleton className="h-4 w-32" />
    <Skeleton className="h-48 w-full" />
  </div>
)
