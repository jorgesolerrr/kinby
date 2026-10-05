// Oxlint JS plugin: locale-sensitive formatting must name its locale.
// Without one, output follows the machine (es-ES prints "1.500", en prints "1,500"),
// so tests pass or fail depending on who runs them.

const LOCALE_METHODS = new Set(["toLocaleString", "toLocaleDateString", "toLocaleTimeString"])
const INTL_FORMATTERS = new Set([
  "NumberFormat",
  "DateTimeFormat",
  "RelativeTimeFormat",
  "ListFormat",
  "PluralRules",
])

const MESSAGE =
  'Pass an explicit locale ("en"), as src/lib/stats.ts does; the machine locale makes output and tests differ between machines.'

/** True when the call omits its locale or passes `undefined` for it. */
function missingLocale(args) {
  const first = args[0]
  return first === undefined || (first.type === "Identifier" && first.name === "undefined")
}

function propertyName(member) {
  if (member.type !== "MemberExpression") return undefined
  if (!member.computed && member.property.type === "Identifier") return member.property.name
  if (member.computed && member.property.type === "Literal") return String(member.property.value)
  return undefined
}

function isIntlFormatter(callee) {
  return (
    callee.type === "MemberExpression" &&
    callee.object.type === "Identifier" &&
    callee.object.name === "Intl" &&
    INTL_FORMATTERS.has(propertyName(callee) ?? "")
  )
}

const explicitLocale = {
  meta: { type: "problem", docs: { description: "Require an explicit locale" } },
  create(context) {
    const check = (node) => {
      const method = propertyName(node.callee)
      const relevant =
        (node.type === "CallExpression" && LOCALE_METHODS.has(method ?? "")) ||
        isIntlFormatter(node.callee)
      if (relevant && missingLocale(node.arguments)) context.report({ node, message: MESSAGE })
    }
    return { CallExpression: check, NewExpression: check }
  },
}

export default {
  meta: { name: "locale" },
  rules: { "explicit-locale": explicitLocale },
}
