/** The most one upload holds, in bytes, as the instance caps it. */
export const UPLOAD_BYTES = 10 * 1024 * 1024

/** A file added to the composer, by where its upload stands. `url` opens it in the browser. */
export type Upload = { key: number; file: File; url: string } & (
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

// What a file's type is when only its name is known, for the kinds a browser shows.
const SHOWN_TYPES: Record<string, string> = {
  png: "image/png",
  jpg: "image/jpeg",
  jpeg: "image/jpeg",
  gif: "image/gif",
  webp: "image/webp",
  pdf: "application/pdf",
}

/** The file at `path`, from its base64 `content` as `upload.get` returns it. */
export function uploadedFile(path: string, content: string): Blob {
  const type = SHOWN_TYPES[path.slice(path.lastIndexOf(".") + 1).toLowerCase()] ?? ""
  return new Blob([Uint8Array.from(atob(content), (char) => char.charCodeAt(0))], { type })
}

/** Whether a browser shows a file of `type` in a tab, rather than saving it. */
export function shownInTab(type: string): boolean {
  return type.startsWith("image/") || type === "application/pdf"
}
