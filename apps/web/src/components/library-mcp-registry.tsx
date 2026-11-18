import type { Client, McpRegistryServer, McpServerEntry } from "@kinby/contract"
import { CircleXIcon, SearchIcon } from "lucide-react"
import { type FormEvent, useState } from "react"

import { NewMcpServer } from "@/components/library-mcp-server"
import { Alert, AlertAction, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { InputGroup, InputGroupAddon, InputGroupInput } from "@/components/ui/input-group"
import {
  Item,
  ItemActions,
  ItemContent,
  ItemDescription,
  ItemGroup,
  ItemTitle,
} from "@/components/ui/item"
import { Spinner } from "@/components/ui/spinner"
import { useHubCall } from "@/hooks/use-hub-call"
import { libraryNameOf } from "@/lib/mcp-servers"
import { reason } from "@/lib/operation"

type Caller = Pick<Client, "call">

type Runnable = McpRegistryServer & { entry: McpServerEntry }

/**
 * A search of the MCP Registry. A result kinby can run opens in the MCP server form, and nothing
 * joins the library until the form adds it.
 */
export function McpRegistrySearch({
  client,
  onAdded,
  onAddByHand,
  onClose,
}: {
  client: Caller
  onAdded: () => void
  onAddByHand: () => void
  onClose: () => void
}) {
  const [words, setWords] = useState("")
  const [found, setFound] = useState<{ words: string; servers: McpRegistryServer[] }>()
  const [chosen, setChosen] = useState<Runnable>()
  const { busy, failure, ask } = useHubCall()

  if (chosen !== undefined) {
    return (
      <NewMcpServer
        client={client}
        chosen={{ ...chosen, name: libraryNameOf(chosen.name) }}
        onAdded={onAdded}
        onClose={() => setChosen(undefined)}
      />
    )
  }
  const search = (event: FormEvent) => {
    event.preventDefault()
    const asked = words.trim()
    setFound(undefined)
    void ask(async () => {
      const { servers } = await client.call("mcp_registry.search", { words: asked })
      setFound({ words: asked, servers })
    })
  }

  return (
    <div className="flex flex-col gap-4">
      <form className="flex gap-2" onSubmit={search}>
        <InputGroup>
          <InputGroupInput
            type="search"
            aria-label="Search the MCP Registry"
            placeholder="Linear, GitHub, a database…"
            value={words}
            onChange={(event) => setWords(event.target.value)}
          />
          <InputGroupAddon>
            <SearchIcon />
          </InputGroupAddon>
        </InputGroup>
        <Button type="submit" disabled={busy || words.trim() === ""}>
          {busy && <Spinner data-icon="inline-start" />}
          Search
        </Button>
        <Button type="button" variant="outline" onClick={onClose}>
          Cancel
        </Button>
      </form>
      {failure !== undefined && (
        <Alert variant="destructive">
          <CircleXIcon />
          <AlertTitle>The MCP Registry could not be reached</AlertTitle>
          <AlertDescription>{reason(failure)}</AlertDescription>
          <AlertAction>
            <Button size="sm" variant="outline" onClick={onAddByHand}>
              Add by hand
            </Button>
          </AlertAction>
        </Alert>
      )}
      {found !== undefined &&
        (found.servers.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            Nothing in the MCP Registry matches &quot;{found.words}&quot;.
          </p>
        ) : (
          <ItemGroup variant="outline" aria-label="MCP Registry results">
            {found.servers.map((server) => (
              <li key={server.name} aria-label={server.name}>
                <Result server={server} onChoose={setChosen} />
              </li>
            ))}
          </ItemGroup>
        ))}
    </div>
  )
}

/** One result: its name, version, how it runs, its description, and why kinby can't run it. */
function Result({
  server,
  onChoose,
}: {
  server: McpRegistryServer
  onChoose: (server: Runnable) => void
}) {
  const { name, version, description, entry, reason } = server
  return (
    <Item size="sm">
      <ItemContent className="min-w-0">
        <ItemTitle>
          <span className="truncate font-mono">{name}</span>
          <Badge variant="outline">{version}</Badge>
          {entry !== null && (
            <Badge variant="secondary">{entry.type === "http" ? "Remote" : entry.command}</Badge>
          )}
        </ItemTitle>
        <ItemDescription>{description}</ItemDescription>
        {reason !== null && <p className="text-sm text-destructive">{reason}</p>}
      </ItemContent>
      {entry !== null && (
        <ItemActions>
          <Button
            size="sm"
            variant="outline"
            aria-label={`Choose ${name}`}
            onClick={() => onChoose({ ...server, entry })}
          >
            Choose
          </Button>
        </ItemActions>
      )}
    </Item>
  )
}
