import { Marker, MarkerContent, MarkerIcon, Spinner } from "@kinby/web"
import {
  BanIcon,
  BrainIcon,
  CheckIcon,
  CircleAlertIcon,
  CircleStopIcon,
  RepeatIcon,
  ShieldAlertIcon,
  UserIcon,
  WrenchIcon,
} from "lucide-react"

export const Variants = () => (
  <div className="flex w-[28rem] flex-col gap-4">
    <Marker>
      <MarkerIcon>
        <WrenchIcon />
      </MarkerIcon>
      <MarkerContent>read_file notes/2026-10.md · 42 ms</MarkerContent>
    </Marker>
    <Marker variant="border">
      <MarkerIcon>
        <UserIcon />
      </MarkerIcon>
      <MarkerContent>You · Today 09:14</MarkerContent>
    </Marker>
    <Marker variant="separator">
      <MarkerContent>Yesterday</MarkerContent>
    </Marker>
    <Marker variant="destructive">
      <MarkerIcon>
        <CircleAlertIcon />
      </MarkerIcon>
      <MarkerContent>Failed: the model provider rate-limited the turn (RATE_LIMITED)</MarkerContent>
    </Marker>
  </div>
)

export const ToolSteps = () => (
  <div className="flex w-[28rem] flex-col gap-2">
    <Marker variant="border">
      <MarkerIcon>
        <RepeatIcon />
      </MarkerIcon>
      <MarkerContent>Morning brief · Today 08:00</MarkerContent>
    </Marker>
    <Marker>
      <MarkerIcon>
        <WrenchIcon />
      </MarkerIcon>
      <MarkerContent>calendar_list today · 318 ms</MarkerContent>
    </Marker>
    <Marker>
      <MarkerIcon>
        <CheckIcon />
      </MarkerIcon>
      <MarkerContent>send_email ana@example.org · approved by you · 1,204 ms</MarkerContent>
    </Marker>
    <Marker>
      <MarkerIcon>
        <BanIcon />
      </MarkerIcon>
      <MarkerContent>bash rm -rf drafts/ · denied by policy: no deletes</MarkerContent>
    </Marker>
    <Marker>
      <MarkerIcon>
        <ShieldAlertIcon />
      </MarkerIcon>
      <MarkerContent>pay_invoice INV-2291 · waiting for you</MarkerContent>
    </Marker>
  </div>
)

export const TurnEnds = () => (
  <div className="flex w-[28rem] flex-col gap-2">
    <Marker>
      <MarkerIcon>
        <Spinner />
      </MarkerIcon>
      <MarkerContent>Working</MarkerContent>
    </Marker>
    <Marker>
      <MarkerIcon>
        <CheckIcon />
      </MarkerIcon>
      <MarkerContent>Done · 4 steps · 12,840 tokens</MarkerContent>
    </Marker>
    <Marker>
      <MarkerIcon>
        <CircleStopIcon />
      </MarkerIcon>
      <MarkerContent>Stopped</MarkerContent>
    </Marker>
    <Marker>
      <MarkerIcon>
        <BrainIcon />
      </MarkerIcon>
      <MarkerContent>
        <a href="#episode">Recapped</a>
      </MarkerContent>
    </Marker>
  </div>
)
