import { describe, expect, it } from "vitest"

import { createThemeStore } from "@/lib/theme"

function fakeSystem(dark: boolean) {
  let listener = () => {}
  return {
    matches: dark,
    addEventListener(_type: "change", next: () => void) {
      listener = next
    },
    change(next: boolean) {
      this.matches = next
      listener()
    },
  }
}

function fakeStorage(stored: string | null = null) {
  const items = new Map<string, string>()
  if (stored !== null) items.set("kinby-theme", stored)
  return {
    getItem: (key: string) => items.get(key) ?? null,
    setItem: (key: string, value: string) => void items.set(key, value),
    items,
  }
}

function darkClass() {
  const applied: boolean[] = []
  return { apply: (dark: boolean) => void applied.push(dark), current: () => applied.at(-1) }
}

describe("theme", () => {
  it("follows the system until the user picks one", () => {
    const system = fakeSystem(true)
    const root = darkClass()
    const store = createThemeStore(fakeStorage(), system, root.apply)

    expect(store.getTheme()).toBe("system")
    expect(root.current()).toBe(true)
    system.change(false)
    expect(root.current()).toBe(false)
  })

  it("keeps the picked theme when the system changes, and remembers it", () => {
    const system = fakeSystem(false)
    const storage = fakeStorage()
    const root = darkClass()
    const store = createThemeStore(storage, system, root.apply)
    let notified = 0
    store.subscribe(() => notified++)

    store.setTheme("dark")
    system.change(false)

    expect(root.current()).toBe(true)
    expect(notified).toBe(1)
    expect(storage.items.get("kinby-theme")).toBe("dark")
  })

  it("restores a stored theme and ignores one it does not know", () => {
    expect(createThemeStore(fakeStorage("light"), fakeSystem(true), () => {}).getTheme()).toBe(
      "light",
    )
    expect(createThemeStore(fakeStorage("sepia"), fakeSystem(true), () => {}).getTheme()).toBe(
      "system",
    )
  })
})
