import { KinbyMark } from "@kinby/web"

export const Sizes = () => (
  <div className="flex items-end gap-4">
    <KinbyMark className="size-4" />
    <KinbyMark className="size-6" />
    <KinbyMark className="size-8" />
    <KinbyMark style={{ width: 48, height: 48 }} />
    <KinbyMark style={{ width: 64, height: 64 }} />
  </div>
)

export const Surfaces = () => (
  <div className="flex gap-3">
    <div className="flex items-center justify-center rounded-xl border bg-card" style={{ width: 96, height: 96 }}>
      <KinbyMark style={{ width: 48, height: 48 }} />
    </div>
    <div className="flex items-center justify-center rounded-xl bg-muted" style={{ width: 96, height: 96 }}>
      <KinbyMark style={{ width: 48, height: 48 }} />
    </div>
    <div className="dark flex items-center justify-center rounded-xl bg-background" style={{ width: 96, height: 96 }}>
      <KinbyMark style={{ width: 48, height: 48 }} />
    </div>
  </div>
)

export const Lockup = () => (
  <div className="flex items-center gap-2">
    <KinbyMark className="size-8" />
    <span className="font-heading text-2xl font-semibold">kinby</span>
  </div>
)
