/** The document the view's frame in `container`, or `container` itself, draws. */
export function drawnPage(container: HTMLElement): Document {
  const frame =
    container instanceof HTMLIFrameElement ? container : container.querySelector("iframe")
  const drawn = frame?.getAttribute("srcdoc")
  if (drawn == null) throw new Error("No view is drawn here.")
  return new DOMParser().parseFromString(drawn, "text/html")
}
