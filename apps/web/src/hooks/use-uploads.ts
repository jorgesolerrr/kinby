import { useRef, useState } from "react"

import { reason } from "@/lib/operation"
import { UPLOAD_BYTES, type Upload } from "@/lib/uploads"

/**
 * The files added to a composer. Each one uploads through `upload` as it is added, unless it is
 * over the cap, and settles as uploaded or failed.
 */
export function useUploads(upload: ((file: File) => Promise<string>) | undefined) {
  const [uploads, setUploads] = useState<Upload[]>([])
  const keys = useRef(0)

  const settle = (settled: Upload) =>
    setUploads((current) => current.map((each) => (each.key === settled.key ? settled : each)))
  const add = (files: ArrayLike<File>) => {
    if (upload === undefined) return
    for (const file of Array.from(files)) {
      const key = ++keys.current
      if (file.size > UPLOAD_BYTES) {
        const failure = "Over 10 MB, so it can't be sent."
        setUploads((current) => [...current, { key, file, status: "failed", failure }])
        continue
      }
      setUploads((current) => [...current, { key, file, status: "uploading" }])
      upload(file).then(
        (path) => settle({ key, file, status: "uploaded", path }),
        (error: unknown) => settle({ key, file, status: "failed", failure: reason(error) }),
      )
    }
  }
  const remove = (key: number) =>
    setUploads((current) => current.filter((each) => each.key !== key))
  /** Drop the uploads a sent message took. */
  const sent = (taken: Upload[]) =>
    setUploads((current) => current.filter((each) => !taken.includes(each)))

  return { uploads, add, remove, sent }
}
