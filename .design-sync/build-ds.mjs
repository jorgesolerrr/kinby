// Prepares kinby's web components as a package the design-sync converter can read (cfg.buildCmd).
// The web app ships no library build, so this writes a gitignored pseudo-package at
// apps/web/node_modules/.ds: an entry re-exporting the ui components and the mark,
// their tsc declarations, and the compiled stylesheet. Run it from the repo root.
import { execSync } from "node:child_process"
import { mkdirSync, readFileSync, readdirSync, rmSync, writeFileSync } from "node:fs"
import { dirname, join, relative } from "node:path"

const web = "apps/web"
const pkg = join(web, "node_modules/.ds")
const sources = [
  ...readdirSync(join(web, "src/components/ui"))
    .filter((f) => f.endsWith(".tsx") && !f.includes(".test."))
    .map((f) => `components/ui/${f.replace(/\.tsx$/, "")}`),
  "components/kinby-mark",
]

rmSync(pkg, { recursive: true, force: true })
mkdirSync(pkg, { recursive: true })

// Declarations: tsc emits the whole app; the "@/" alias is rewritten to relative paths.
execSync(
  "bun x tsc -p tsconfig.app.json --noEmit false --declaration --emitDeclarationOnly --rootDir src --outDir node_modules/.ds/types",
  { cwd: web, stdio: "inherit" },
)
const types = join(pkg, "types")
const walk = (dir) =>
  readdirSync(dir, { withFileTypes: true }).flatMap((e) =>
    e.isDirectory() ? walk(join(dir, e.name)) : [join(dir, e.name)],
  )
for (const file of walk(types).filter((f) => f.endsWith(".d.ts"))) {
  const text = readFileSync(file, "utf8")
  const fixed = text.replace(/(["'])@\/([^"']+)\1/g, (_, q, p) => {
    const rel = relative(dirname(file), join(types, p)).replaceAll("\\", "/")
    return `${q}${rel.startsWith(".") ? rel : `./${rel}`}${q}`
  })
  if (fixed !== text) writeFileSync(file, fixed)
}

// recharts rides along as Kinby.Recharts: ChartContainer passes the chart size through recharts'
// context, so previews and designs must draw with the bundle's own copy.
const reexports = (prefix) =>
  [...sources.map((s) => `export * from "${prefix}${s}"`), `export * as Recharts from "recharts"`].join("\n") + "\n"
writeFileSync(join(types, "index.d.ts"), reexports("./"))
writeFileSync(join(pkg, "index.ts"), reexports("../../src/"))
writeFileSync(
  join(pkg, "package.json"),
  JSON.stringify({ name: "@kinby/web", version: "0.0.0", module: "index.ts", types: "types/index.d.ts" }, null, 2),
)

// Stylesheet: index.css compiled by Tailwind. The fontsource @font-face rules are dropped:
// their url()s point into node_modules. The converter ships the same faces from cfg.extraFonts.
const css = join(pkg, "kinby.css")
execSync(`bunx @tailwindcss/cli@4.3.3 -i .design-sync/kinby.css -o ${css}`, { stdio: "inherit" })
writeFileSync(css, readFileSync(css, "utf8").replace(/@font-face\s*\{[^}]*\}\s*/g, ""))
