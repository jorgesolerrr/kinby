/** The most one upload holds, in bytes, as the instance caps it. */
export const UPLOAD_BYTES = 10 * 1024 * 1024

/** A file added to the composer, by where its upload stands. */
export type Upload = { key: number; file: File } & (
  | { status: "uploading" }
  | { status: "uploaded"; path: string }
  | { status: "failed"; failure: string }
)

/** The content of `file`, base64-encoded as `upload.put` takes it. */
export function base64(file: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => {
      const url = reader.result as string
      resolve(url.slice(url.indexOf(",") + 1))
    }
    reader.onerror = () => reject(reader.error)
    reader.readAsDataURL(file)
  })
}

/** `bytes` as a person reads a file's size. */
export function fileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}
