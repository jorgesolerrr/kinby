// PROTOTYPE (#1450), throwaway. Three variants of a factory's steps and the step editor,
// switchable via `?variant=`, on the throwaway route /prototype/factory-steps. Fixed data from the
// shipped software factory, no hub, so `bun run dev` alone opens it. The screen open lives in
// `?at=`: factory, step:<id>, add:<after>, or run.
import { useParam } from "./nav"
import { PrototypeSwitcher } from "./shared"
import { VariantA } from "./variant-a"
import { VariantB } from "./variant-b"
import { VariantC } from "./variant-c"

export const PROTOTYPE_PATH = "/prototype/factory-steps"

const VARIANTS = [
  { key: "A", name: "Rail and page", component: VariantA },
  { key: "B", name: "Table and panel", component: VariantB },
  { key: "C", name: "Map and cards", component: VariantC },
]

export function FactoryStepsPrototype() {
  const key = useParam("variant") ?? VARIANTS[0].key
  const Variant = (VARIANTS.find((v) => v.key === key) ?? VARIANTS[0]).component
  return (
    <>
      <Variant key={key} />
      <PrototypeSwitcher variants={VARIANTS} />
    </>
  )
}
