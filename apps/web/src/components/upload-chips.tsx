import { CircleAlertIcon, FileIcon, XIcon } from "lucide-react"

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
import { fileSize, type Upload } from "@/lib/uploads"

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
          <li key={upload.key} aria-label={name}>
            <Item variant="outline" size="xs" className="max-w-64 flex-nowrap">
              <ItemMedia variant="icon">
                {upload.status === "uploading" ? (
                  <Spinner aria-label={`Uploading ${name}`} />
                ) : upload.status === "failed" ? (
                  <CircleAlertIcon />
                ) : (
                  <FileIcon />
                )}
              </ItemMedia>
              <ItemContent className="min-w-0">
                <ItemTitle className="break-all">{name}</ItemTitle>
                <ItemDescription>
                  {upload.status === "failed" ? upload.failure : fileSize(upload.file.size)}
                </ItemDescription>
              </ItemContent>
              <ItemActions>
                <Button
                  variant="ghost"
                  size="icon-xs"
                  aria-label={`Remove ${name}`}
                  onClick={() => onRemove(upload.key)}
                >
                  <XIcon />
                </Button>
              </ItemActions>
            </Item>
          </li>
        )
      })}
    </ul>
  )
}
