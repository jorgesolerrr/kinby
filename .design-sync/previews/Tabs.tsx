import { Tabs, TabsContent, TabsList, TabsTrigger } from "@kinby/web"
import { BrainIcon, KeyRoundIcon, RepeatIcon, WrenchIcon } from "lucide-react"

export const Line = () => (
  <Tabs defaultValue="spend" style={{ width: 480 }}>
    <TabsList variant="line">
      <TabsTrigger value="overview">Overview</TabsTrigger>
      <TabsTrigger value="spend">Spend</TabsTrigger>
      <TabsTrigger value="origin">Origin</TabsTrigger>
      <TabsTrigger value="quality">Quality</TabsTrigger>
    </TabsList>
    <TabsContent value="spend">
      <p className="pt-4 text-muted-foreground">
        $42.18 of API cost this month, plus 31 runs on your Claude plan.
      </p>
    </TabsContent>
  </Tabs>
)

export const Default = () => (
  <Tabs defaultValue="routines" style={{ width: 480 }}>
    <TabsList>
      <TabsTrigger value="skills">Skills</TabsTrigger>
      <TabsTrigger value="routines">Routines</TabsTrigger>
      <TabsTrigger value="secrets">Secrets</TabsTrigger>
    </TabsList>
    <TabsContent value="routines">
      <p className="pt-4 text-muted-foreground">
        Morning brief runs every weekday at 8:00. Weekly review runs Friday at 17:00.
      </p>
    </TabsContent>
  </Tabs>
)

export const WithIcons = () => (
  <Tabs defaultValue="memory">
    <TabsList>
      <TabsTrigger value="memory">
        <BrainIcon />
        Memory
      </TabsTrigger>
      <TabsTrigger value="tools">
        <WrenchIcon />
        Tools
      </TabsTrigger>
      <TabsTrigger value="routines">
        <RepeatIcon />
        Routines
      </TabsTrigger>
      <TabsTrigger value="secrets" disabled>
        <KeyRoundIcon />
        Secrets
      </TabsTrigger>
    </TabsList>
  </Tabs>
)

export const Vertical = () => (
  <Tabs defaultValue="core" orientation="vertical" style={{ width: 440 }}>
    <TabsList>
      <TabsTrigger value="core">Core</TabsTrigger>
      <TabsTrigger value="permissions">Permissions</TabsTrigger>
      <TabsTrigger value="manifest">Manifest</TabsTrigger>
    </TabsList>
    <TabsContent value="core">
      <p className="text-muted-foreground">
        Running core 0.14.2, the hub's revision. Updates follow the hub.
      </p>
    </TabsContent>
  </Tabs>
)
