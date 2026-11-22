import { useCallback, useSyncExternalStore } from "react"

import { draftUploads, keepUploads, watchUploads } from "@/lib/drafts"
import { reason } from "@/lib/operation"
import { UPLOAD_BYTES, type Upload } from "@/lib/uploads"

let keys = 0

/**
 * The files added to the unsent message under `draftKey`, kept with its draft. Each one uploads
 * through `upload` as it is added, unless it is over the cap, and settles as uploaded or failed,
 * whether or not the composer is still open.
 */
export function useUploads(
  draftKey: string,
  upload: ((file: File) => Promise<string>) | undefined,
) {
  const uploads = useSyncExternalStore(
    watchUploads,
    useCallback(() => draftUploads(draftKey), [draftKey]),
  )
  const change = (edit: (current: Upload[]) => Upload[]) => keepUploads(draftKey, edit)

  const settle = (settled: Upload) =>
    change((current) => current.map((each) => (each.key === settled.key ? settled : each)))
  const add = (files: ArrayLike<File>) => {
    if (upload === undefined) return
    for (const file of Array.from(files)) {
      const added = { key: ++keys, file, url: URL.createObjectURL(file) }
      if (file.size > UPLOAD_BYTES) {
        const failure = "Over 10 MB, so it can't be sent."
        change((current) => [...current, { ...added, status: "failed", failure }])
        continue
      }
      change((current) => [...current, { ...added, status: "uploading" }])
      upload(file).then(
        (path) => settle({ ...added, status: "uploaded", path }),
        (error: unknown) => settle({ ...added, status: "failed", failure: reason(error) }),
      )
    }
  }
  const drop = (dropped: (upload: Upload) => boolean) => {
    for (const upload of uploads.filter(dropped)) URL.revokeObjectURL(upload.url)
    change((current) => current.filter((each) => !dropped(each)))
  }
  const remove = (key: number) => drop((upload) => upload.key === key)
  /** Drop the uploads a sent message took. */
  const sent = (taken: Upload[]) => drop((upload) => taken.includes(upload))

  return { uploads, add, remove, sent }
}
