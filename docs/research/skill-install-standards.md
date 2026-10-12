# Skill install standards: what exists to adopt for the hub library

Date: 2026-10-12.
Question: which vendor-neutral formats can kinby adopt as is for (a) addressing a skill in a git repository, (b) recording what an instance picked, and (c) a library that later also holds tools and MCP servers?

Scope: the Agent Skills specification, the `skills` CLI, Claude Code plugins and marketplaces, the MCP Registry, and Codex. Hermes Agent and OpenClaw are covered in separate notes.

Every claim links to the source that owns it. Claims with no primary source are marked **unverified**. Source code links for the `skills` CLI are pinned to commit `13e4063` (package version 1.7.2, read on 2026-10-12). This repository pins `skills@1.7.0` in `package.json`; where 1.7.0 differs, the note says so.

## Short answer

1. **Only the skill folder is a standard.** The Agent Skills specification fixes `SKILL.md`, its frontmatter and the folder conventions. It says nothing about distribution, versions, pinning, locks or registries.
2. **The `skills` CLI is the common tool, not a standard.** Its source string (`owner/repo`, tree URL, `#ref`, `@skill`), its repository scan and its two lock files are implementation details of one MIT-licensed npm package. The project lock pins content by a SHA-256 of the folder. It never records a commit.
3. **Claude Code's marketplace format is the only documented format that bundles several kinds** (skills, agents, hooks, MCP servers, more) and that pins a git source to a commit (`ref` plus `sha`). It is vendor-owned, but the `skills` CLI and Codex both read or mirror it.
4. **The MCP Registry `server.json` is the standard for describing an MCP server.** A version is immutable once published, and a client pins by fetching `/v0.1/servers/{serverName}/versions/{version}`.

## 1. Agent Skills specification

Source: <https://agentskills.io/specification>, repository <https://github.com/agentskills/agentskills>.

### What it standardises

A skill is a directory with a `SKILL.md` at its root. The specification gives this layout:

```
skill-name/
├── SKILL.md          # Required: metadata + instructions
├── scripts/          # Optional: executable code
├── references/       # Optional: documentation
├── assets/           # Optional: templates, resources
└── ...               # Any additional files or directories
```

`SKILL.md` is YAML frontmatter followed by Markdown. The frontmatter fields ([specification, Frontmatter](https://agentskills.io/specification#frontmatter)):

| Field | Required | Constraint |
| - | - | - |
| `name` | Yes | 1 to 64 characters, lowercase `a-z`, `0-9` and hyphens, no leading, trailing or doubled hyphen, and it "Must match the parent directory name" |
| `description` | Yes | 1 to 1024 characters, says what the skill does and when to use it |
| `license` | No | License name or a bundled license file |
| `compatibility` | No | Up to 500 characters of environment requirements |
| `metadata` | No | Map of string keys to string values, for properties the specification does not define |
| `allowed-tools` | No | Space-separated pre-approved tools, marked experimental |

`scripts/`, `references/` and `assets/` are recommendations, not requirements: "The conventions below are recommendations for organizing common types of content" ([Optional directories](https://agentskills.io/specification#optional-directories)). File references are relative to the skill root. Loading is progressive: name and description at startup, the body on activation, other files on demand ([Progressive disclosure](https://agentskills.io/specification#progressive-disclosure)).

### What it leaves unspecified

- **Version.** There is no version field. The specification's own example puts a version inside the free-form map, `metadata: { author: example-org, version: "1.0" }`, which no client is required to read ([`metadata` field](https://agentskills.io/specification#metadata-field)).
- **Where skills live on disk.** The client guide says so directly: "the Agent Skills specification does not mandate where skill directories live (it only defines what goes inside them)". It calls `.agents/skills/` a "widely-adopted convention", not a rule ([Adding skills support, Where to scan](https://agentskills.io/client-implementation/adding-skills-support#where-to-scan)).
- **Distribution, installation, updates, pinning, lock files, registries.** The specification page has no section on any of them. The site's page index lists nine pages (overview, specification, client showcase, four authoring guides, scripts, client implementation) and none covers distribution ([llms.txt](https://agentskills.io/llms.txt)). The client guide only says a hosted agent has to provision skills "from an external source — for example, cloning a configuration repository, accepting skill URLs or packages through your agent's settings, or letting users upload skill directories" ([Cloud-hosted and sandboxed agents](https://agentskills.io/client-implementation/adding-skills-support#cloud-hosted-and-sandboxed-agents)).
- **Name collisions.** Guidance only: project-level overrides user-level, and within a scope "either first-found or last-found is acceptable" ([Handling name collisions](https://agentskills.io/client-implementation/adding-skills-support#handling-name-collisions)).

The same guide tells clients to validate leniently: a name that does not match its directory is a warning, not a rejection ([Lenient validation](https://agentskills.io/client-implementation/adding-skills-support#lenient-validation)).

## 2. The `skills` CLI

Source: <https://github.com/vercel-labs/skills> (npm package `skills`, directory at <https://skills.sh>). It describes itself as "The CLI for the open agent skills ecosystem" ([README](https://github.com/vercel-labs/skills/blob/13e4063a1cf913f5606d57d42ab83a86f5001e04/README.md)). Nothing in the Agent Skills specification refers to it.

### How a source is addressed

All parsing is in [`src/source-parser.ts`](https://github.com/vercel-labs/skills/blob/13e4063a1cf913f5606d57d42ab83a86f5001e04/src/source-parser.ts) (`parseSource`). The accepted forms, in the order the parser tries them:

| Input | Parsed as |
| - | - |
| `./path`, `../path`, `.`, an absolute path, `C:\path` | `local` |
| `github:owner/repo`, `gitlab:owner/repo` | prefix shorthand, re-parsed |
| `raw.githubusercontent.com/...`, `github.com/o/r/archive/...`, `/releases/download/...` | `download` (a `SKILL.md`, zip or tarball fetched directly) |
| `https://github.com/owner/repo/tree/<ref>/<path>` | `github` with `ref` and `subpath` |
| `https://github.com/owner/repo/tree/<ref>` | `github` with `ref` |
| `https://github.com/owner/repo` | `github` |
| `https://<host>/<group>/<repo>/-/tree/<ref>/<path>` | `gitlab` with `ref` and `subpath` |
| `https://dev.azure.com/org/project/_git/repo?path=/x&version=GBmain` | `git` with `ref` and `subpath` |
| `owner/repo@skill-name` | `github` with a skill-name filter |
| `owner/repo` or `owner/repo/path/to/skill` | `github` with optional `subpath` |
| any other `http(s)` URL | `well-known`: looks for `/.well-known/agent-skills/index.json`, then `/.well-known/skills/index.json` ([`src/providers/wellknown.ts`](https://github.com/vercel-labs/skills/blob/13e4063a1cf913f5606d57d42ab83a86f5001e04/src/providers/wellknown.ts)) |
| anything else, such as `git@host:owner/repo.git` | `git`, cloned as is |

A ref goes in a URL fragment: `<source>#<ref>`, or `<source>#<ref>@<skill>` to also name one skill (`parseFragmentRef`). The fragment is read as a ref only for git-like sources. A subpath containing `..` is rejected (`sanitizeSubpath`).

A ref is a branch or tag. A full 40-character commit SHA also works, as a fallback: the clone first runs `git clone --depth 1 --branch <ref>`, and if that fails and the ref is a full SHA it fetches the commit directly ([`src/git.ts`](https://github.com/vercel-labs/skills/blob/13e4063a1cf913f5606d57d42ab83a86f5001e04/src/git.ts), `cloneRepo`). So a commit can be given as input, but the tool never resolves a branch to a commit and records it.

For public GitHub sources the CLI may skip git altogether and download a pre-built snapshot from `skills.sh/api/download/<owner>/<repo>/<slug>`, a hosted service ([`src/blob.ts`](https://github.com/vercel-labs/skills/blob/13e4063a1cf913f5606d57d42ab83a86f5001e04/src/blob.ts)). `SKILLS_DOWNLOAD_URL` overrides the host.

### How it discovers skills in a repository

[`src/skills.ts`](https://github.com/vercel-labs/skills/blob/13e4063a1cf913f5606d57d42ab83a86f5001e04/src/skills.ts), `discoverSkills`:

1. If the search path itself holds a `SKILL.md`, that is the one skill and the scan stops (unless `--full-depth`).
2. Otherwise it scans, in order: the root (one level), `skills/`, `skills/.curated/`, `skills/.experimental/`, `skills/.system/`, then every agent's project skills directory (`.agents/skills`, `.claude/skills`, `.codex/skills`, thirty in all). Each of these containers is walked up to three levels deep, and the walk stops below a folder that has a `SKILL.md`.
3. It adds skill paths declared in `.claude-plugin/marketplace.json` or `.claude-plugin/plugin.json` ([README, Plugin Manifest Discovery](https://github.com/vercel-labs/skills/blob/13e4063a1cf913f5606d57d42ab83a86f5001e04/README.md#plugin-manifest-discovery)).
4. If nothing was found, it searches the whole tree to depth 5, skipping `node_modules`, `.git`, `dist`, `build` and `__pycache__`.

A skill needs string `name` and `description` frontmatter or it is skipped with a warning (`parseSkillMd`). `metadata.internal: true` hides a skill unless it is named explicitly. Skills are identified by frontmatter `name`, and the first one seen with a given name wins.

### Where it installs

[`src/installer.ts`](https://github.com/vercel-labs/skills/blob/13e4063a1cf913f5606d57d42ab83a86f5001e04/src/installer.ts) and the README's [Supported Agents table](https://github.com/vercel-labs/skills/blob/13e4063a1cf913f5606d57d42ab83a86f5001e04/README.md#supported-agents):

- **Scope.** Project by default (`./<agent>/skills/`), global with `-g` (`~/<agent>/skills/`).
- **Canonical copy plus symlinks (default).** The skill is copied to `.agents/skills/<name>` (project) or `~/.agents/skills/<name>` (global), and each agent's own directory gets a symlink to it. On Windows the link is a junction. If the symlink fails, it falls back to a copy.
- **`--copy`.** No canonical directory. The files are copied straight into each agent's directory. This repository uses `--copy` in `skills:sync`.
- **Per agent path.** Many agents ("universal" ones: Codex, Cursor, Gemini CLI, GitHub Copilot, OpenCode, Amp) read `.agents/skills/` directly. Others have their own: Claude Code `.claude/skills/`, OpenClaw `skills/`, Hermes Agent `.hermes/skills/`.
- **What is copied.** Everything in the folder except `metadata.json`, `.git`, `__pycache__` and `__pypackages__`. Symlinks in the source are dereferenced.
- **Install replaces.** Before copying, the target directory is removed and recreated (`cleanAndCreateDirectory`: `rm(path, { recursive: true, force: true })`).

### The project lock: `skills-lock.json`

Defined in [`src/local-lock.ts`](https://github.com/vercel-labs/skills/blob/13e4063a1cf913f5606d57d42ab83a86f5001e04/src/local-lock.ts). It is written to the current working directory on a project-scope install and "is meant to be checked into version control". The type, with every field:

```ts
export interface LocalSkillLockEntry {
  source: string;        // owner/repo, npm package name, local path
  sourceUrl?: string;    // original remote URL, when source was normalized (git and gitlab sources only)
  ref?: string;          // branch or tag used for installation
  sourceType: string;    // "github", "gitlab", "git", "well-known", "node_modules", "local"
  skillPath?: string;    // path to SKILL.md inside the source repo
  version?: string;      // npm package version (node_modules sources only)
  via?: string;          // package whose package.json `skills` field requested it
  computedHash: string;  // SHA-256 of the skill folder
  subagents?: string[];  // Eve subagent placement
  wellKnownDigest?: string;
}

export interface LocalSkillLockFile {
  version: number;                              // currently 1
  skills: Record<string, LocalSkillLockEntry>;  // keyed by skill name, sorted
}
```

The file is JSON with two-space indentation, keys sorted by skill name, and a trailing newline. The source comments give the reason for the shape: "Intentionally minimal and timestamp-free to minimize merge conflicts."

A real entry, from this repository's `skills-lock.json`:

```json
{
  "version": 1,
  "skills": {
    "adversarial-review": {
      "source": "jorgesolerrr/skills",
      "sourceType": "github",
      "skillPath": "skills/mine/adversarial-review/SKILL.md",
      "computedHash": "74744362002a12609f64fe378a1cb61221b428c1932db532fe0becbfcdf44381"
    }
  }
}
```

There is no `ref` key because none was given on the command line: the entry builder sets `ref: parsed.ref` and JSON drops `undefined` ([`src/add.ts`](https://github.com/vercel-labs/skills/blob/13e4063a1cf913f5606d57d42ab83a86f5001e04/src/add.ts), `projectLockEntry`).

What the project lock does not hold: a commit SHA, a timestamp, the installed path, the list of agents installed to, or the install mode. The same skill name cannot be locked twice, since the name is the key.

### How `computedHash` is computed

`computeSkillFolderHash` in [`src/local-lock.ts`](https://github.com/vercel-labs/skills/blob/13e4063a1cf913f5606d57d42ab83a86f5001e04/src/local-lock.ts):

1. Collect every regular file under the skill folder, recursively. Skip directories named `.git` and `node_modules`.
2. Take each file's path relative to the skill folder, with forward slashes.
3. Sort the files by that path with JavaScript's `String.prototype.localeCompare` (no locale argument).
4. Feed one SHA-256, for each file in order, the path string and then the file's raw bytes.
5. The lock value is the hex digest.

There is no separator between a path and its content, and none between files. Line endings are not normalized, which is why this repository's `skills:sync` forces `core.autocrlf=false` for the clone.

Two things to know before reimplementing it:

- **The sort is locale collation, not byte order.** `tests/test_repository_skills.py` in this repository reproduces it in Python with a hand-written collation table. A hash rule kinby owns would sort by code point instead.
- **What gets hashed changed between releases.** In 1.7.0 the hash is taken over the installed directory ([`add.ts` at v1.7.0](https://github.com/vercel-labs/skills/blob/v1.7.0/src/add.ts): `computeSkillFolderHash(installDir)`). At 1.7.2 it is taken over the source: the folder in the clone, or the hash the skills.sh snapshot reports (`sourceSkillHash`). For a plain copy the two are equal, since the installer excludes only `metadata.json` and three directory names. For the `eve` agent they differ, because the installer rewrites `SKILL.md` frontmatter on the way in.

Observed in this repository: `adversarial-review`, `tdd` and `unslop` have different `computedHash` values in `skills-lock.json` and `skills-lock.coder.json`, although both name the same `source` and `skillPath`. The cause is **unverified**. Nothing in the lock says which commit either was taken from, so the file alone cannot settle it.

### The global lock: `.skill-lock.json`

Defined in [`src/skill-lock.ts`](https://github.com/vercel-labs/skills/blob/13e4063a1cf913f5606d57d42ab83a86f5001e04/src/skill-lock.ts). Path: `$XDG_STATE_HOME/skills/.skill-lock.json` if `XDG_STATE_HOME` is set, otherwise `~/.agents/.skill-lock.json`. Schema version 3. A file with an older version is discarded and rebuilt empty.

```ts
export interface SkillLockEntry {
  source: string;           // normalized, e.g. "owner/repo"
  sourceType: string;
  sourceUrl: string;        // original URL, for re-fetching
  ref?: string;
  skillPath?: string;
  skillFolderHash: string;  // GitHub tree SHA of the skill folder
  installedAt: string;      // ISO timestamp
  updatedAt: string;        // ISO timestamp
  pluginName?: string;
  sourceBaseUrl?: string;
  wellKnownDigest?: string;
}

export interface SkillLockFile {
  version: number;                          // currently 3
  skills: Record<string, SkillLockEntry>;
  dismissed?: { findSkillsPrompt?: boolean };
  lastSelectedAgents?: string[];
}
```

A real entry, from `~/.agents/.skill-lock.json` on the maintainer's machine:

```json
"supabase-postgres-best-practices": {
  "source": "supabase/agent-skills",
  "sourceType": "github",
  "sourceUrl": "https://github.com/supabase/agent-skills.git",
  "skillPath": "skills/supabase-postgres-best-practices/SKILL.md",
  "skillFolderHash": "95fe6d29daaf88aa9bc3b505ca4defe19ceabf92",
  "pluginName": "postgres-best-practices",
  "installedAt": "2026-03-24T23:59:40.603Z",
  "updatedAt": "2026-03-24T23:59:40.603Z"
}
```

`skillFolderHash` is a different hash from `computedHash`. For a GitHub source it is the git tree object SHA of the skill folder, read from the GitHub Trees API (`getSkillFolderHashFromTree` in [`src/blob.ts`](https://github.com/vercel-labs/skills/blob/13e4063a1cf913f5606d57d42ab83a86f5001e04/src/blob.ts)), or from `git rev-parse HEAD:<folder>` in a clone (`getGitTreeHash` in `src/git.ts`). For other sources it falls back to the SHA-256 folder hash, and it can be an empty string.

Neither hash is a commit. A git tree SHA identifies the folder's content, so it survives commits that do not touch the folder, but it cannot be checked out without knowing a commit that contains it.

### How `update` and `check` decide something changed

`check`, `update` and `upgrade` are one command: all three call `runUpdate` ([`src/cli.ts`](https://github.com/vercel-labs/skills/blob/13e4063a1cf913f5606d57d42ab83a86f5001e04/src/cli.ts)). There is no read-only check and no diff output.

Project scope, `updateProjectSkills` in [`src/update.ts`](https://github.com/vercel-labs/skills/blob/13e4063a1cf913f5606d57d42ab83a86f5001e04/src/update.ts):

1. Read `skills-lock.json`. Skip `local` and `node_modules` entries. Entries without `skillPath` are reported as not updatable.
2. Group by source and `ref`. Shallow-clone each group at its `ref`, or at the default branch when there is none.
3. Run discovery over the clone and match each locked skill to a path. A skill no longer found is reported as deleted upstream. Interactive runs offer to remove it. Non-interactive runs skip the deletion.
4. Hash each skill's folder in the clone with `computeSkillFolderHash` and compare it with the locked `computedHash`.
5. Equal and not moved: up to date. Otherwise re-run `skills add <source>/<folder>#<ref> --skill <name> -y`, which replaces the installed folder and rewrites the lock entry.

Global scope, `updateGlobalSkills`: the same, except that for a GitHub source it reads the repository tree from the GitHub API and compares the folder's tree SHA with `skillFolderHash`, cloning only if the API is unavailable or a path moved.

Consequences:

- **The comparison is upstream against the lock.** The installed copy on disk is never read during an update.
- **A local edit is invisible and gets overwritten.** If upstream changed, the reinstall deletes the folder and copies the new one. If upstream did not change, the edited copy stays and nothing reports it. There is no merge, no backup and no warning.
- **Update means "latest on the ref".** With no ref, that is the default branch. There is no way to hold a skill at a commit other than installing with a SHA as the ref.
- **Detecting local edits is left to the consumer.** This repository does it in `tests/test_repository_skills.py`, which recomputes the hash of each vendored folder and fails `bun run check` when it stops matching the lock.

`experimental_install` restores a project from its lock by re-running the install for each source ([`src/install.ts`](https://github.com/vercel-labs/skills/blob/13e4063a1cf913f5606d57d42ab83a86f5001e04/src/install.ts)). It does not compare what it fetched with `computedHash`, so it restores whatever the ref points at today, not what was locked. It installs only into `.agents/skills/`.

`remove` deletes the installed folders and the lock entry ([README, `skills remove`](https://github.com/vercel-labs/skills/blob/13e4063a1cf913f5606d57d42ab83a86f5001e04/README.md#skills-remove)).

## 3. Claude Code plugins and marketplaces

Source: <https://code.claude.com/docs/en/plugins/marketplace-reference>, <https://code.claude.com/docs/en/plugins/host-marketplace>, <https://code.claude.com/docs/en/plugins/loading>, <https://code.claude.com/docs/en/plugins/components>. These pages are the only source: the format is defined by Claude Code's documentation, with no separate specification and no public implementation to read.

### A marketplace is a catalog in a git repository

A marketplace is a directory with `.claude-plugin/marketplace.json`. `name`, `owner` and `plugins` are required. Each plugin entry needs `name` and `source` ([Top-level fields](https://code.claude.com/docs/en/plugins/marketplace-reference#top-level-fields), [Plugin entries](https://code.claude.com/docs/en/plugins/marketplace-reference#plugin-entries)):

```json
{
  "name": "my-marketplace",
  "description": "Plugins for my team",
  "owner": { "name": "Your Name" },
  "plugins": [
    {
      "name": "my-first-plugin",
      "source": "./plugins/my-first-plugin",
      "description": "A greeting plugin to learn the basics"
    }
  ]
}
```

Adding one: `claude plugin marketplace add <input>`, where the input is `owner/repo`, `owner/repo@ref` or `owner/repo#ref` (GitHub), a git URL with optional `#ref`, an `https` URL to a bare `marketplace.json`, or a local directory or file ([Marketplace sources](https://code.claude.com/docs/en/plugins/marketplace-reference#marketplace-sources)). The marketplace is identified by the `name` inside the file, not by the repository name, and a user can register only one marketplace per name. A plugin is then installed as `<plugin>@<marketplace>`.

### A plugin entry's source, and how it pins

The entry's `source` is a relative path string or an object ([Plugin sources](https://code.claude.com/docs/en/plugins/marketplace-reference#plugin-sources)):

| Type | Fields | Meaning |
| - | - | - |
| relative path | the string | a directory inside the marketplace repository, must start with `./` |
| `github` | `repo`, `ref`, `sha` | a GitHub repository, `owner/repo` |
| `url` | `url`, `ref`, `sha` | any git repository by URL |
| `git-subdir` | `url`, `path`, `ref`, `sha` | one subdirectory of a git repository, fetched with a sparse checkout |
| `npm` | `package`, `version`, `registry` | an npm package |
| `archive` | `url`, `sha256` | a zip over HTTPS |
| `command` | `command`, `timeout`, `mode` | a directory printed by a local command |

The pinned form, quoted from the reference:

```json
{
  "name": "formatter",
  "source": {
    "source": "github",
    "repo": "your-org/formatter",
    "ref": "v2.0.0",
    "sha": "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0"
  }
}
```

`ref` is "a branch or tag", defaulting to the default branch. `sha` is "a full 40-character lowercase commit SHA", and "When you set both `ref` and `sha`, Claude Code checks out `sha`". `git-subdir` is the documented way to address one folder of someone else's repository:

```json
{
  "name": "formatter",
  "source": {
    "source": "git-subdir",
    "url": "https://github.com/your-org/monorepo.git",
    "path": "tools/formatter"
  }
}
```

### How a plugin bundles several kinds

A plugin is a directory. Each kind of component has a default location in it ([Add components to a plugin](https://code.claude.com/docs/en/plugins/components)):

| Kind | Location |
| - | - |
| manifest | `.claude-plugin/plugin.json` |
| skills | `skills/<name>/SKILL.md` |
| commands | `commands/<name>.md` |
| agents | `agents/<name>.md` |
| hooks | `hooks/hooks.json` |
| MCP servers | `.mcp.json` |
| LSP servers | `.lsp.json` |
| monitors | `monitors/monitors.json` |
| output styles, themes | `output-styles/`, `themes/` |
| executables | `bin/` |
| default settings | `settings.json` |

Skills inside a plugin are ordinary Agent Skills folders. An MCP server in a plugin is a launch configuration, not an install record: it names a command and arguments, with `${CLAUDE_PLUGIN_ROOT}` standing for the plugin's install directory. The plugin is the unit that is installed, versioned and removed. A user cannot take one skill out of a plugin.

The manifest is optional. When the fetched plugin has no `plugin.json`, the marketplace entry is the manifest, and the entry may declare `skills`, `agents`, `hooks`, `mcpServers` and the rest itself ([How an entry combines with plugin.json](https://code.claude.com/docs/en/plugins/marketplace-reference#entry-and-plugin-json)). This is the path the `skills` CLI reads when it discovers skills from a marketplace file.

### Versions and updates

The version of an installed plugin is computed in this order ([How Claude Code computes the version](https://code.claude.com/docs/en/plugins/loading#how-claude-code-computes-the-version)):

1. `version` in the plugin's `plugin.json`.
2. `version` in the marketplace entry.
3. Otherwise, from the source: for `github`, `url` and `git-subdir`, "The commit SHA of the source, shortened to 12 characters"; for `archive`, the SHA-256 digest shortened to 12 characters; for `npm`, `unknown`.

An update happens only when the computed version differs from the installed one. So a manifest that pins `"version": "1.0.0"` holds users on the cached copy across any number of commits, and omitting `version` everywhere makes users track commits ([Release a new version](https://code.claude.com/docs/en/plugins/host-marketplace#release-a-new-version)). There are no version ranges for direct installs and no release channels: "one marketplace serves one version of each plugin at a time". Auto-update is off by default for third-party marketplaces.

### Where installs are recorded

Under `~/.claude/plugins/` ([Find plugins on disk](https://code.claude.com/docs/en/plugins/loading#find-plugins-on-disk)):

- `known_marketplaces.json`: each fetched marketplace with its `source`, `installLocation`, `lastUpdated` and `autoUpdate`.
- `installed_plugins.json`: "records each install with its `scope`, `installPath`, and `version`".
- `cache/<marketplace>/<plugin>/<version>/`: one copied directory per installed version.
- `data/<plugin-id>/`: the plugin's persistent data, kept across updates.

The documentation names the fields but does not print the files. The two files as found on the maintainer's machine (Claude Code, October 2026), with every key kept and the names, paths and commit replaced by placeholders:

```json
{
  "version": 2,
  "plugins": {
    "my-plugin@my-marketplace": [
      {
        "scope": "user",
        "installPath": "<home>/.claude/plugins/cache/my-marketplace/my-plugin/0.2.2",
        "version": "0.2.2",
        "installedAt": "2026-10-09T19:26:41.942Z",
        "lastUpdated": "2026-10-09T20:29:50.863Z",
        "gitCommitSha": "<40-character commit SHA>"
      }
    ]
  }
}
```

```json
{
  "my-marketplace": {
    "source": { "source": "directory", "path": "<path to the marketplace directory>" },
    "installLocation": "<path to the marketplace directory>",
    "lastUpdated": "2026-10-09T20:29:50.113Z"
  }
}
```

`installedAt`, `lastUpdated` and `gitCommitSha` appear in the real file and are not described in the documentation, so their exact meaning is **unverified**. The record is keyed by `<plugin>@<marketplace>` and holds a list, one item per scope.

Whether a plugin is on is a separate record: `enabledPlugins` in a settings file, at user, project, local or managed scope ([Find where a plugin is enabled](https://code.claude.com/docs/en/plugins/loading#find-where-a-plugin-is-enabled)). Declared, fetched and loaded are three stages with three stores.

An installed plugin is a copy in the cache, one directory per version. An update writes a new version directory and marks the old one for removal after 14 days. The documentation does not say what happens to a file edited inside the cache. Since an update switches to a new directory, an edit would be left behind in the old one, but that reading is **unverified**.

Removal: `renames` in the marketplace maps an old plugin name to a new one or to `null`, and `forceRemoveDeletedPlugins: true` uninstalls delisted plugins from users' machines ([Rename or remove a plugin](https://code.claude.com/docs/en/plugins/host-marketplace#rename-or-remove-a-plugin)).

## 4. MCP Registry

Source: <https://github.com/modelcontextprotocol/registry>, live at <https://registry.modelcontextprotocol.io>. Marked as preview: "Breaking changes or data resets may occur before general availability" ([About](https://github.com/modelcontextprotocol/registry/blob/main/docs/modelcontextprotocol-io/about.mdx)).

The registry holds metadata, not code: "The MCP Registry hosts metadata that points to those packages" on npm, PyPI, NuGet, Docker/OCI or as MCPB files. An entry is a `server.json`. `name`, `description` and `version` are required ([OpenAPI spec](https://github.com/modelcontextprotocol/registry/blob/main/docs/reference/api/openapi.yaml), `ServerDetail`). An example from the format reference ([server.json Format Specification](https://github.com/modelcontextprotocol/registry/blob/main/docs/reference/server-json/generic-server-json.md)):

```json
{
  "$schema": "https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json",
  "name": "io.modelcontextprotocol.anonymous/brave-search",
  "description": "MCP server for Brave Search API integration",
  "title": "Brave Search",
  "repository": {
    "url": "https://github.com/modelcontextprotocol/servers",
    "source": "github"
  },
  "version": "1.0.2",
  "packages": [
    {
      "registryType": "npm",
      "registryBaseUrl": "https://registry.npmjs.org",
      "identifier": "@modelcontextprotocol/server-brave-search",
      "version": "1.0.2",
      "transport": { "type": "stdio" },
      "environmentVariables": [
        { "name": "BRAVE_API_KEY", "description": "Brave Search API Key", "isRequired": true, "isSecret": true }
      ]
    }
  ]
}
```

What an entry records: a reverse-DNS name whose namespace is verified, a version, where the server is (`packages` for a local server, `remotes` with a URL for a hosted one), how to run it (transport, arguments, environment variables with `isRequired` and `isSecret`), and an optional `repository` with a `subfolder` for monorepos.

How a version is pinned ([Versioning](https://github.com/modelcontextprotocol/registry/blob/main/docs/modelcontextprotocol-io/versioning.mdx)):

- "The version string **MUST** be unique for each publication of the server. Once published, the version string (and other metadata) cannot be changed."
- Version ranges are prohibited in `server.json`. Semantic versioning is recommended, not required.
- A client reads one version at `GET /v0.1/servers/{serverName}/versions/{version}`, where the special version `latest` returns the newest.
- A package may carry `fileSha256`. It is required for MCPB packages, and "If present, MCP clients must validate the downloaded file matches the hash before running".

The registry specifies no client-side lock file. A client pins by storing the server name and version, and for a package the package version or digest. Host applications are expected to consume a downstream registry that implements the same OpenAPI interface, and a private registry can implement it too.

## 5. Others

**OpenAI Codex.** Codex reads the Agent Skills format and scans `.agents/skills` from the working directory up to the repository root, then `$HOME/.agents/skills` and `/etc/codex/skills` ([Build skills](https://developers.openai.com/codex/skills)). It adds one optional file the specification does not have, `agents/openai.yaml`, which can declare MCP dependencies for a skill (`dependencies.tools` with `type: "mcp"` and a URL). For distribution it points away from loose skill folders: "If you want to distribute a reusable skill, bundle two or more skills together, or ship a skill alongside a connector, package them as a plugin." That is a second vendor reaching the same design as Claude Code: the skill is the authoring unit, and a plugin that bundles skills with MCP configuration is the distribution unit. Whether Codex's plugin manifest is file-compatible with Claude Code's was not checked and is **unverified**.

**Cursor and the other `.agents/skills` readers.** The `skills` CLI's agent table lists Cursor, Gemini CLI, GitHub Copilot, OpenCode and others as reading the project directory `.agents/skills/`. No primary source was found showing any agent other than the `skills` CLI reading `skills-lock.json` or `.skill-lock.json`. The lock is the CLI's own bookkeeping, not something agents consume. This is an absence of evidence, so treat it as **unverified**.

## What is standard and what is only common

| Thing | Status |
| - | - |
| `SKILL.md` frontmatter, folder as the unit, `scripts/` `references/` `assets/` | Standard: Agent Skills specification |
| `.agents/skills/` as the shared location | Convention, named as such by the specification's own guide |
| `owner/repo`, tree URLs, `#ref`, `@skill` | One tool's syntax (`skills` CLI) |
| Scanning `skills/` and agent directories for skills in a repository | One tool's behaviour (`skills` CLI) |
| `skills-lock.json`, `computedHash` | One tool's file, schema version 1, hash input changed between 1.7.0 and 1.7.2 |
| `.claude-plugin/marketplace.json`, `plugin.json` | Vendor format (Anthropic), documented, read by the `skills` CLI |
| `server.json`, registry API | Standard for MCP servers, in preview |
| A skill's version | Not standardised anywhere |
| A commit pin for a skill | Exists only in the marketplace format (`sha`) |

## Recommendation for the hub library

**(a) Addressing a skill in a git repository.** Take the input syntax of the `skills` CLI, since it is what users already paste (`npx skills add owner/repo`, a GitHub tree URL, `#ref`). Store it in the shape of the marketplace `git-subdir` source: `url`, `path`, `ref`, `sha`. That shape has a place for the commit, which the CLI's lock lacks, and it is documented. For the required layout, follow the specification only: a folder with a `SKILL.md` carrying `name` and `description`. Use the CLI's scan order (`SKILL.md` at the given path, then `skills/`, then the known agent directories) to list what a repository offers.

**(b) The record of what an instance picked.** Keep the field names of `skills-lock.json` (`source`, `sourceType`, `ref`, `skillPath`, `computedHash`) so the record stays readable next to the files this repository already has, and add the one field the format is missing: the resolved commit SHA. With the commit recorded, "update" can show a real diff between two commits, and a restore gives back what was picked. Keep a content hash of the instance's copy as well, and compare it on update: that is the only way to notice a local edit, which the CLI never does. If kinby computes the hash itself, define the rule in kinby (sorted by code point) and do not promise equality with `computedHash`, whose input already changed once between patch releases.

**(c) A library that also holds tools and MCP servers.** The marketplace model is the one to follow: a catalog of named entries, each with a typed `source` that can pin a commit, and a kind per component with a fixed place in a directory. For MCP servers, do not invent an entry format: store the registry's `server.json` and pin by name and version, plus `fileSha256` or a package version where there is one. Both formats can be adopted without adopting Claude Code's unit of installation. kinby picks single skills, and a plugin cannot be split.

Open points this note does not settle:

- Whether the hub should accept a Claude Code marketplace repository as a library source directly, reading `marketplace.json` as the `skills` CLI does.
- Whether a pick copies or references. Both tools copy. The `skills` CLI's symlink mode points at a canonical copy on the same machine, not at the source.
