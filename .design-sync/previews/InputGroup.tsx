import {
  InputGroup,
  InputGroupAddon,
  InputGroupButton,
  InputGroupInput,
  InputGroupText,
  InputGroupTextarea,
} from "@kinby/web"
import { ArrowUpIcon, CopyIcon, KeyRoundIcon, SearchIcon } from "lucide-react"

export const Composer = () => (
  <div className="w-[28rem]">
    <InputGroup>
      <InputGroupTextarea placeholder="Message kinby" />
      <InputGroupAddon align="block-end">
        <InputGroupButton size="icon-sm" variant="default" className="ml-auto" aria-label="Send">
          <ArrowUpIcon />
        </InputGroupButton>
      </InputGroupAddon>
    </InputGroup>
  </div>
)

export const WithIcon = () => (
  <div className="w-80">
    <InputGroup>
      <InputGroupInput placeholder="Search threads" />
      <InputGroupAddon>
        <SearchIcon />
      </InputGroupAddon>
    </InputGroup>
  </div>
)

export const TextAndButton = () => (
  <div className="flex w-80 flex-col gap-3">
    <InputGroup>
      <InputGroupAddon>
        <InputGroupText>$</InputGroupText>
      </InputGroupAddon>
      <InputGroupInput defaultValue="25.00" />
      <InputGroupAddon align="inline-end">
        <InputGroupText>per month</InputGroupText>
      </InputGroupAddon>
    </InputGroup>
    <InputGroup>
      <InputGroupAddon>
        <KeyRoundIcon />
      </InputGroupAddon>
      <InputGroupInput defaultValue="ANTHROPIC_API_KEY" readOnly />
      <InputGroupAddon align="inline-end">
        <InputGroupButton size="icon-xs" aria-label="Copy">
          <CopyIcon />
        </InputGroupButton>
      </InputGroupAddon>
    </InputGroup>
  </div>
)

export const Invalid = () => (
  <div className="w-80">
    <InputGroup>
      <InputGroupAddon>
        <InputGroupText>https://</InputGroupText>
      </InputGroupAddon>
      <InputGroupInput aria-invalid defaultValue="hub.local:port" />
    </InputGroup>
  </div>
)

export const Disabled = () => (
  <div className="w-80">
    <InputGroup>
      <InputGroupAddon>
        <SearchIcon />
      </InputGroupAddon>
      <InputGroupInput disabled placeholder="Search memory" />
    </InputGroup>
  </div>
)
