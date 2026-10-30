/** Focus `field`, unless the user is typing in another one, like the title in the header. */
export function takeFocus(field: HTMLElement | null) {
  const active = document.activeElement
  if (active instanceof HTMLInputElement || active instanceof HTMLTextAreaElement) return
  field?.focus()
}
