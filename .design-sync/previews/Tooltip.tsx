import { Button, Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@kinby/web"
import { BrainIcon, RefreshCwIcon, SettingsIcon } from "lucide-react"

export const OnIconButton = () => (
  <TooltipProvider>
    <div className="flex justify-center" style={{ paddingTop: "4rem" }}>
      <Tooltip open>
        <TooltipTrigger render={<Button variant="outline" size="icon" aria-label="Restart" />}>
          <RefreshCwIcon />
        </TooltipTrigger>
        <TooltipContent>Restart the instance</TooltipContent>
      </Tooltip>
    </div>
  </TooltipProvider>
)

export const Sides = () => (
  <TooltipProvider>
    <div className="flex justify-center" style={{ gap: "6rem", paddingTop: "4rem" }}>
      <Tooltip open>
        <TooltipTrigger render={<Button variant="ghost" size="icon" aria-label="Memory" />}>
          <BrainIcon />
        </TooltipTrigger>
        <TooltipContent side="bottom">Memory</TooltipContent>
      </Tooltip>
      <Tooltip open>
        <TooltipTrigger render={<Button variant="ghost" size="icon" aria-label="Settings" />}>
          <SettingsIcon />
        </TooltipTrigger>
        <TooltipContent side="right">Settings</TooltipContent>
      </Tooltip>
    </div>
  </TooltipProvider>
)

export const LongText = () => (
  <TooltipProvider>
    <div className="flex justify-center" style={{ paddingTop: "5rem" }}>
      <Tooltip open>
        <TooltipTrigger render={<Button variant="secondary" size="sm" />}>Next run</TooltipTrigger>
        <TooltipContent>morning-brief runs at 8:00 tomorrow on the research instance</TooltipContent>
      </Tooltip>
    </div>
  </TooltipProvider>
)
