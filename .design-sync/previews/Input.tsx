import { Input } from "@kinby/web"

export const Default = () => (
  <div className="flex w-80 flex-col gap-3">
    <Input placeholder="Name this instance" />
    <Input defaultValue="research-assistant" />
  </div>
)

export const Types = () => (
  <div className="flex w-80 flex-col gap-3">
    <Input type="email" defaultValue="ana@example.com" />
    <Input type="password" defaultValue="hub-passphrase" />
    <Input type="number" defaultValue={25} />
  </div>
)

export const Invalid = () => (
  <div className="w-80">
    <Input aria-invalid defaultValue="Morning Brief!" />
  </div>
)

export const Disabled = () => (
  <div className="w-80">
    <Input disabled defaultValue="kinby-hub-01" />
  </div>
)
