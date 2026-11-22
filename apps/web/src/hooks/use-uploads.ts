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
      const key = ++keys
      if (file.size > UPLOAD_BYTES) {
        const failure = "Over 10 MB, so it can't be sent."
        change((current) => [...current, { key, file, status: "failed", failure }])
        continue
      }
      change((current) => [...current, { key, file, status: "uploading" }])
      upload(file).then(
        (path) => settle({ key, file, status: "uploaded", path }),
        (error: unknown) => settle({ key, file, status: "failed", failure: reason(error) }),
      )
    }
  }
  const remove = (key: number) => change((current) => current.filter((each) => each.key !== key))
  /** Drop the uploads a sent message took. */
  const sent = (taken: Upload[]) =>
    change((current) => current.filter((each) => !taken.includes(each)))

  return { uploads, add, remove, sent }
}
