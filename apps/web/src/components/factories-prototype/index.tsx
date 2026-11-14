// PROTOTYPE (#1299), throwaway. Three variants of a Factories area, switchable via `?variant=`, on
// the throwaway route /prototype/factories. It runs on fixed data with no hub, so `bun run dev`
// alone opens it. Which screen is open lives in `?at=`.
import { useParam } from "./nav"
import { PrototypeSwitcher } from "./shared"
import { VariantA } from "./variant-a"
import { VariantB } from "./variant-b"
import { VariantC } from "./variant-c"

export const PROTOTYPE_PATH = "/prototype/factories"

const VARIANTS = [
  { key: "A", name: "Directory", component: VariantA },
  { key: "B", name: "Lines in the sidebar", component: VariantB },
  { key: "C", name: "Owner-centric", component: VariantC },
]

export function FactoriesPrototype() {
  const key = useParam("variant") ?? "A"
  const Variant = (VARIANTS.find((v) => v.key === key) ?? VARIANTS[0]).component
  return (
    <>
      <Variant key={key} />
      <PrototypeSwitcher variants={VARIANTS} />
    </>
  )
}
