import { CallError, type InstanceClient } from "@kinby/contract"
import { CircleAlertIcon, FileIcon, FileXIcon, XIcon } from "lucide-react"
import { type ReactNode, useEffect, useState } from "react"

import { Button } from "@/components/ui/button"
import {
  Item,
  ItemActions,
  ItemContent,
  ItemDescription,
  ItemMedia,
  ItemTitle,
} from "@/components/ui/item"
import { Spinner } from "@/components/ui/spinner"
import { reason } from "@/lib/operation"
import { fileSize, shownInTab, type Upload, uploadedFile } from "@/lib/uploads"

/** The files added to a message: each one's name, its size or why it failed, and remove. */
export function UploadChips({
  uploads,
  onRemove,
}: {
  uploads: Upload[]
  onRemove: (key: number) => void
}) {
  return (
    <ul aria-label="Added files" className="flex flex-wrap gap-2">
      {uploads.map((upload) => {
        const name = upload.file.name
        return (
          <FileChip
            key={upload.key}
            name={name}
            url={upload.url}
            type={upload.file.type}
            icon={
              upload.status === "uploading" ? (
                <Spinner aria-label={`Uploading ${name}`} />
              ) : upload.status === "failed" ? (
                <CircleAlertIcon />
              ) : undefined
            }
            description={upload.status === "failed" ? upload.failure : fileSize(upload.file.size)}
            action={
              <Button
                variant="ghost"
                size="icon-xs"
                className="relative"
                aria-label={`Remove ${name}`}
                onClick={() => onRemove(upload.key)}
              >
                <XIcon />
              </Button>
            }
          />
        )
      })}
    </ul>
  )
}

/** The files a sent message gave, each read back from the instance's workspace. */
export function SentUploads({
  paths,
  client,
}: {
  paths: string[]
  client: Pick<InstanceClient, "call">
}) {
  return (
    <ul aria-label="Sent files" className="flex flex-wrap justify-end gap-2">
      {paths.map((path) => (
        <SentUpload key={path} path={path} client={client} />
      ))}
    </ul>
  )
}

type Read = { file: Blob; url: string } | { gone: true } | { failure: string }

function SentUpload({ path, client }: { path: string; client: Pick<InstanceClient, "call"> }) {
  const [read, setRead] = useState<Read>()
  useEffect(() => {
    let open = true
    let url: string | undefined
    client.call("upload.get", { path }).then(
      ({ content }) => {
        if (!open) return
        const file = uploadedFile(path, content)
        url = URL.createObjectURL(file)
        setRead({ file, url })
      },
      (error: unknown) =>
        open &&
        setRead(
          error instanceof CallError && error.code === "NOT_FOUND"
            ? { gone: true }
            : { failure: reason(error) },
        ),
    )
    return () => {
      open = false
      if (url !== undefined) URL.revokeObjectURL(url)
    }
  }, [client, path])
  const name = path.slice(path.lastIndexOf("/") + 1)
  if (read === undefined) {
    return <FileChip name={name} icon={<Spinner aria-label={`Reading ${name}`} />} />
  }
  if ("file" in read) {
    return (
      <FileChip
        name={name}
        url={read.url}
        type={read.file.type}
        description={fileSize(read.file.size)}
      />
    )
  }
  return "gone" in read ? (
    <FileChip name={name} icon={<FileXIcon />} description="Gone from the workspace" />
  ) : (
    <FileChip name={name} icon={<CircleAlertIcon />} description={read.failure} />
  )
}

/**
 * A file as a chip: `icon`, or else the thumbnail of an image, then its name and `description`,
 * then `action`. With `url`, a click on the chip opens the file, of media `type`.
 */
function FileChip({
  name,
  url,
  type = "",
  icon,
  description,
  action,
}: {
  name: string
  url?: string
  type?: string
  icon?: ReactNode
  description?: string
  action?: ReactNode
}) {
  const thumbnail = icon === undefined && url !== undefined && type.startsWith("image/")
  return (
    <li aria-label={name}>
      <Item variant="outline" size="xs" className="relative max-w-64 flex-nowrap">
        {thumbnail ? (
          <ItemMedia variant="image">
            <img src={url} alt={name} />
          </ItemMedia>
        ) : (
          <ItemMedia variant="icon">{icon ?? <FileIcon />}</ItemMedia>
        )}
        <ItemContent className="min-w-0">
          <ItemTitle className="break-all">
            {url === undefined ? (
              name
            ) : (
              // The link stretches over the chip, so a click anywhere on it opens the file.
              <a
                href={url}
                target="_blank"
                rel="noreferrer"
                download={shownInTab(type) ? undefined : name}
                className="after:absolute after:inset-0"
              >
                {name}
              </a>
            )}
          </ItemTitle>
          {description !== undefined && <ItemDescription>{description}</ItemDescription>}
        </ItemContent>
        {action !== undefined && <ItemActions>{action}</ItemActions>}
      </Item>
    </li>
  )
}
