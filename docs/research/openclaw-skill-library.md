# OpenClaw and ClawHub: how skills get in, get picked, and get updated

Date: 2026-10-12.

Question: kinby is designing a hub-level library that holds pickable things (skills first, later tools, MCP servers and factories). How do OpenClaw and its registry ClawHub handle the two open decisions: how something enters the library, and what a pick does (copy or reference, pin, update, local edits, removal)?

This note builds on `docs/research/self-extending-agents.md` (branch `research/self-extending-agents`), which covers how OpenClaw agents create skills themselves (Skill Workshop). That part is not repeated here.

Sources are the two repositories, read at fixed commits:

- OpenClaw: [`openclaw/openclaw@73f9f4e`](https://github.com/openclaw/openclaw/tree/73f9f4e54257784cac122cae7466a44cc8f098a3). `O:path` below means `https://github.com/openclaw/openclaw/blob/73f9f4e54257784cac122cae7466a44cc8f098a3/path`.
- ClawHub: [`openclaw/clawhub@3635eb2`](https://github.com/openclaw/clawhub/tree/3635eb2fcc3b80cc3d6ee6a6455cb493d998c4d1). `C:path` below means `https://github.com/openclaw/clawhub/blob/3635eb2fcc3b80cc3d6ee6a6455cb493d998c4d1/path`.

Claims rest on docs and source read at those commits. Nothing was run. Where a claim comes from source code the file is named. Claims with no primary source are marked **unverified**.

## Short answer

1. **A pick is a copy, never a reference.** `openclaw skills install` copies the skill folder into the agent's `<workspace>/skills/<slug>` or, with `--global`, into the shared `~/.openclaw/skills/<slug>`. Sharing between agents comes from which directory the copy lands in, plus a per-agent name allowlist in config. There is no per-agent link to a shared store.
2. **Three sources, three tracking levels.** Registry (`@owner/slug`, semver plus tags), git (`git:owner/repo@ref`, resolved to a commit) and local path. Only registry installs can be updated. Git and path installs record their origin and must be reinstalled to refresh.
3. **Two files pin a registry install.** `<root>/.clawhub/lock.json` lists every tracked skill in that root. `<skill>/.clawhub/origin.json` sits inside each installed skill. Both carry the version, the registry URL and content digests.
4. **Local edits are found by hash.** Update recomputes a digest of the installed tree and compares it with the one recorded at install. A mismatch stops the update unless `--force` is passed. There is no merge and no diff in the CLI.
5. **Trust is a registry verdict checked before download**, plus an optional operator policy command that runs on the staged files. `--force` overrides neither.
6. **Plugins, MCP servers and skills use three separate mechanisms.** Plugins have their own install ledger in SQLite. MCP servers are config entries. An experimental "Claw" package bundles an agent with exact-version skills, plugins and MCP servers.
7. **OpenClaw follows the Agent Skills spec** (`SKILL.md` with `name` and `description` frontmatter) and puts its own fields under `metadata.openclaw`.
8. **ClawHub's own repo does not use ClawHub's lock for its development skills.** It commits a `skills-lock.json` in the `skills` CLI format (source repo, path, content hash), the same format kinby already uses.

So the problem is solved, but by two conventions and not one standard. The `SKILL.md` format is standard. The lock format is not: `.clawhub/lock.json` is ClawHub's, `skills-lock.json` is the `skills` CLI's, and Agent Skills itself defines no lock.

## Skill locations and precedence

OpenClaw loads file-backed skills from these roots, highest precedence first. On a name collision the higher root wins ([O:docs/tools/skills.md](https://github.com/openclaw/openclaw/blob/73f9f4e54257784cac122cae7466a44cc8f098a3/docs/tools/skills.md), "Loading order").

| Priority | Source | Path | Visible to |
| --- | --- | --- | --- |
| 1 | Workspace skills | `<workspace>/skills` | that agent |
| 2 | Project agent skills | `<workspace>/.agents/skills` | that workspace's agent |
| 3 | Personal agent skills | `~/.agents/skills` (default state only) | agents on the default state dir |
| 4 | Managed / local skills | `<state-dir>/skills` (`~/.openclaw/skills`) | all agents on that state dir |
| 5 | Workshop skills | `<state-dir>/agents/<agentId>/agent/workshop-skills` | that agent |
| 6 | Bundled skills | shipped with the install | all agents |
| 6 | Custodian skills | shipped, under `custodian-skills/` | only the system agent |
| 7 | Extra directories | `skills.load.extraDirs` plus plugin skills | all agents on that config |

Details that matter for a library design:

- **Discovery is by `SKILL.md`, up to six levels deep.** The folder path is only for organisation. The skill's name comes from the `name` frontmatter field, and allowlists match on that name (same page).
- **Location and visibility are separate controls.** `agents.defaults.skills` is a shared baseline allowlist. `agents.entries.<id>.skills` replaces it (no merge). `[]` means no skills. Omitting both leaves everything visible ([O:docs/tools/skills-config.md](https://github.com/openclaw/openclaw/blob/73f9f4e54257784cac122cae7466a44cc8f098a3/docs/tools/skills-config.md), "Agent allowlists").
- **The allowlist is not a security boundary.** The docs say an agent with host `exec` can still read files in other skill roots (same section, the Warning).
- **Per-skill switches live in config**, keyed by skill name: `skills.entries.<key>.enabled`, `.env`, `.apiKey`, `.config`. `skills.allowBundled` is an allowlist for bundled skills only (same page).
- **Shipped skills are release-versioned and never tracked.** Bundled and Custodian skills come with the package. Custodian skills load "at the bundled-skill precedence tier, but only for the agent resolved by `agents.defaults.systemAgent.agentId`" ([O:docs/tools/custodian-skills.md](https://github.com/openclaw/openclaw/blob/73f9f4e54257784cac122cae7466a44cc8f098a3/docs/tools/custodian-skills.md)). This is the closest thing to kinby's "shipped with the product, for the home instance only".
- **Sessions snapshot the skill list at start.** A file watcher bumps the snapshot when a `SKILL.md` changes, and the new list applies on the next turn ([O:docs/tools/skills.md](https://github.com/openclaw/openclaw/blob/73f9f4e54257784cac122cae7466a44cc8f098a3/docs/tools/skills.md), "Snapshots and refresh").

### The second model: a managed library with immutable revisions

OpenClaw also has a newer "personal skill library" for a Gateway shared by several identified operators. It is a reference model, and it sits beside the copy model above (same page, "Personal skills on a shared Gateway").

- Saving "publishes a complete immutable revision: `SKILL.md` and every supporting file". The revision hash covers "portable file paths, exact content, sizes, and executable flags".
- Storage: records in `state/openclaw.sqlite`, bundles under `skill-library/<skill-id>/revisions/<revision-hash>/`. The docs say not to edit those directories.
- **A session pins skill IDs and revisions.** New revisions reach new sessions. An existing session moves only on an explicit attach or refresh. Rollback selects a retained revision. Removing a skill from the library "excludes it from new selections without deleting a revision already selected by a session".
- Edits use optimistic concurrency: `openclaw skills library update <skill-id> ./SKILL.md --expected-revision <revision-hash>`, and "a conflict requires reviewing the newer content, not a force overwrite" ([O:docs/cli/skills.md](https://github.com/openclaw/openclaw/blob/73f9f4e54257784cac122cae7466a44cc8f098a3/docs/cli/skills.md), "Personal skill library").
- A ClawHub skill enters it by import, which is again a copy: `openclaw skills library import @owner/<slug> --clawhub --slug my-skill`.
- Limits: 256 files, 1 MiB per file, 8 MiB per bundle. A new session selects at most 64 library skills.

The selection unit here is the session, not the agent. Whether an agent can hold a standing pinned selection is not stated in the docs (**unverified**).

## How a skill gets in

### Addressing

| Form | Meaning | Source |
| --- | --- | --- |
| `@owner/<slug>` | ClawHub skill, latest version | [O:docs/cli/skills.md](https://github.com/openclaw/openclaw/blob/73f9f4e54257784cac122cae7466a44cc8f098a3/docs/cli/skills.md) |
| `@owner/<slug> --version <version>` | exact ClawHub version | same |
| bare `<slug>` | accepted for installed or unambiguous skills | same |
| `skills-sh:<owner>/<repo>/<slug>` | external skills.sh listing; ClawHub resolves it to "its exact synchronized GitHub commit" | same |
| `git:owner/repo[@ref]` | unmanaged git clone; ref may be a branch, tag or commit | same |
| `./path/to/skill` | local directory copy | same |

Hosted ClawHub skills use semver. New skills publish as `1.0.0`, changed skills default to the next patch version, and tags such as `latest` point at a version ([C:docs/cli.md](https://github.com/openclaw/clawhub/blob/3635eb2fcc3b80cc3d6ee6a6455cb493d998c4d1/docs/cli.md), `skill publish`, `sync`, `skill tag`). Published versions are immutable ([C:docs/how-it-works.md](https://github.com/openclaw/clawhub/blob/3635eb2fcc3b80cc3d6ee6a6455cb493d998c4d1/docs/how-it-works.md)).

ClawHub also has **GitHub-backed skills**, where the registry holds metadata and a scan verdict but the bytes come from the upstream repository. For these "the commit is the install version", identified by `githubCurrentCommit` and `githubCurrentContentHash`, and the scan status is bound to "that exact content hash" ([C:specs/github-backed-skills.md](https://github.com/openclaw/clawhub/blob/3635eb2fcc3b80cc3d6ee6a6455cb493d998c4d1/specs/github-backed-skills.md)). The `clawhub` CLI writes the commit as `installedVersion` for such installs (`resolvedVersion = githubInstall.github.commit` in [C:packages/clawhub/src/cli/commands/skills.ts](https://github.com/openclaw/clawhub/blob/3635eb2fcc3b80cc3d6ee6a6455cb493d998c4d1/packages/clawhub/src/cli/commands/skills.ts)).

### Git as a direct source

Yes, with limits ([O:docs/cli/skills.md](https://github.com/openclaw/openclaw/blob/73f9f4e54257784cac122cae7466a44cc8f098a3/docs/cli/skills.md); [O:src/skills/lifecycle/source-install.ts](https://github.com/openclaw/openclaw/blob/73f9f4e54257784cac122cae7466a44cc8f098a3/src/skills/lifecycle/source-install.ts)):

- **Layout: `SKILL.md` at the repository root.** One repo, one skill. There is no `--path` for a skill in a subfolder. Multi-skill repos go through ClawHub (`skills-sh:owner/repo/slug`, or a GitHub-backed catalog whose `root` defaults to `skills/`).
- **Who fetches:** the OpenClaw process clones into a temp directory with a sanitised environment (`GIT_CONFIG_NOSYSTEM=1`, `GIT_TERMINAL_PROMPT=0`), resolves the ref to a commit, copies everything except `.git`, then installs the copy.
- **Slug:** `SKILL.md` frontmatter `name` when valid, else the repo or directory name, else `--as <slug>`.
- **Validation before copy:** the root `SKILL.md` must load under the normal rules (frontmatter with a description, within `skills.limits.maxSkillFileBytes`).
- **No update path.** "`openclaw skills update` tracks ClawHub installs only — reinstall Git or local sources to refresh them" ([O:docs/tools/skills.md](https://github.com/openclaw/openclaw/blob/73f9f4e54257784cac122cae7466a44cc8f098a3/docs/tools/skills.md), "Install details").
- "Skill installs do not support npm package specs or zip/archive paths." A zip upload path exists for Gateway clients behind `skills.install.allowUploadedArchives: true`, off by default.

### Where it lands

`install`, `update` and `verify` target the active workspace `skills/` directory by default. `--agent <id>` picks one agent's workspace. `--global` targets the shared managed directory and cannot be combined with `--agent` ([O:docs/cli/skills.md](https://github.com/openclaw/openclaw/blob/73f9f4e54257784cac122cae7466a44cc8f098a3/docs/cli/skills.md)).

### Publishing

`clawhub skill publish <path>` and `clawhub sync` push local skill folders to the registry. `sync` scans for folders containing `SKILL.md`, compares each folder's fingerprint with ClawHub and publishes only new or changed ones. It is "one-way publish only" and does not install or update ([C:docs/cli.md](https://github.com/openclaw/clawhub/blob/3635eb2fcc3b80cc3d6ee6a6455cb493d998c4d1/docs/cli.md)). Publishing releases a skill under `MIT-0`.

## Pinning and provenance: the exact files

### Registry installs: `.clawhub/lock.json` and `.clawhub/origin.json`

"ClawHub tracking uses `.clawhub/lock.json` in the workspace and `.clawhub/origin.json` in each installed skill" ([O:docs/tools/skills.md](https://github.com/openclaw/openclaw/blob/73f9f4e54257784cac122cae7466a44cc8f098a3/docs/tools/skills.md)). For a `--global` install the root is the state directory, so the lock is `~/.openclaw/.clawhub/lock.json` (inferred from the uninstall instructions, which pass `--workdir ~/.openclaw`).

The types, from [O:src/skills/lifecycle/workspace-types.ts](https://github.com/openclaw/openclaw/blob/73f9f4e54257784cac122cae7466a44cc8f098a3/src/skills/lifecycle/workspace-types.ts):

```ts
export type ClawHubSkillOrigin = ClawHubSkillRef & {   // slug, ownerHandle?, requestedReference?, trustState?
  version: 1;
  registry: string;
  installedVersion: string;
  installedAt: number;
  sourceUrl?: string;
  artifact?: { kind: "archive" | "clawpack"; sha256: string; integrity: string };
  skillFile?: { path: string; sha256: string };
  fileTreeSha256?: string;
};

export type ClawHubSkillsLockfile = {
  version: 1;
  skills: Record<string, ClawHubSkillLockEntry>;   // origin fields, with `version` in place of `installedVersion`, plus `verification?`
};
```

The writer is `recordClawHubSkillInstall` in [O:src/skills/lifecycle/clawhub-store.ts](https://github.com/openclaw/openclaw/blob/73f9f4e54257784cac122cae7466a44cc8f098a3/src/skills/lifecycle/clawhub-store.ts). It writes the origin file, then copies the same fields into the lock entry keyed by slug and adds the `verification` envelope when there is one.

No real install was run for this note, so the two examples below are assembled from those types and from the repo's own fixture ([O:src/skills/lifecycle/clawhub.test-support.ts](https://github.com/openclaw/openclaw/blob/73f9f4e54257784cac122cae7466a44cc8f098a3/src/skills/lifecycle/clawhub.test-support.ts), `writeClawHubOriginFixture`). The field names and nesting are exact. The values, including hashes, are illustrative.

`<workspace>/skills/weather/.clawhub/origin.json`:

```json
{
  "version": 1,
  "registry": "https://clawhub.ai",
  "slug": "weather",
  "ownerHandle": "acme",
  "installedVersion": "1.2.3",
  "installedAt": 1760227200000,
  "artifact": { "kind": "archive", "sha256": "<hex>", "integrity": "sha256-<base64>" },
  "skillFile": { "path": "SKILL.md", "sha256": "<64 hex>" },
  "fileTreeSha256": "sha256:<64 hex>"
}
```

`<workspace>/.clawhub/lock.json`:

```json
{
  "version": 1,
  "skills": {
    "weather": {
      "version": "1.2.3",
      "registry": "https://clawhub.ai",
      "installedAt": 1760227200000,
      "ownerHandle": "acme",
      "artifact": { "kind": "archive", "sha256": "<hex>", "integrity": "sha256-<base64>" },
      "skillFile": { "path": "SKILL.md", "sha256": "<64 hex>" },
      "fileTreeSha256": "sha256:<64 hex>",
      "verification": { "schema": "clawhub.skill.verify.v1", "ok": true, "decision": "pass", "reasons": [] }
    }
  }
}
```

How the digests are computed:

- `skillFile.sha256` is the SHA-256 of the root skill file's bytes.
- `fileTreeSha256` is `sha256:` plus the SHA-256 of a JSON array of every directory and file entry (`{path, type, sha256?}`), with `.clawhub` and `.clawdhub` excluded. Symlinks and hard-linked files make the digest fail ([O:src/skills/lifecycle/skill-tree-digest.ts](https://github.com/openclaw/openclaw/blob/73f9f4e54257784cac122cae7466a44cc8f098a3/src/skills/lifecycle/skill-tree-digest.ts)).
- `artifact` records the downloaded archive's own hash, separate from the installed tree.
- For `skills-sh:` installs, `requestedReference` keeps the original reference and `trustState` keeps the "not scanned by ClawHub" state, so both survive updates.

### The standalone `clawhub` CLI writes a different dialect

The `clawhub` npm CLI writes the same two paths with fewer and different fields. Its lock schema ([C:packages/schema/src/schemas.ts](https://github.com/openclaw/clawhub/blob/3635eb2fcc3b80cc3d6ee6a6455cb493d998c4d1/packages/schema/src/schemas.ts)):

```ts
export const LockfileSchema = type({
  version: "1",
  skills: {
    "[string]": {
      version: "string|null",
      installedAt: "number",
      ownerHandle: "string?",
      sourceRef: "string?",
      pinned: "boolean?",
      pinReason: "string?",
    },
  },
});
```

Its origin type ([C:packages/clawhub/src/skills.ts](https://github.com/openclaw/clawhub/blob/3635eb2fcc3b80cc3d6ee6a6455cb493d998c4d1/packages/clawhub/src/skills.ts)) has `version`, `registry`, `slug`, `ownerHandle?`, `sourceRef?`, `sourceKind?`, `sourceRepository?`, `sourcePath?`, `sourceUrl?`, `canonicalRef?`, `clawhubScan?`, `trustLabel?`, `artifactIdentity?`, `installedVersion`, `installedAt` and `fingerprint?`.

`fingerprint` is the SHA-256 of the sorted lines `<path>:<sha256>` for every file (`buildSkillFingerprint`, same file). The registry knows the fingerprint of every published version, so the CLI can ask "which version is this folder?".

Only this CLI has `clawhub pin <skill> [--reason <text>]` and `unpin`. A pinned skill is skipped by `update --all`, rejected by `update <skill>` and by `install --force` ([C:docs/cli.md](https://github.com/openclaw/clawhub/blob/3635eb2fcc3b80cc3d6ee6a6455cb493d998c4d1/docs/cli.md)). OpenClaw's lock type has no `pinned` field. Whether OpenClaw's `skills update` honours a pin written by the `clawhub` CLI was not checked (**unverified**).

### Git and path installs: `.openclaw/source-origin.json`

No lock entry. One file inside the skill ([O:src/skills/lifecycle/source-install-metadata.ts](https://github.com/openclaw/openclaw/blob/73f9f4e54257784cac122cae7466a44cc8f098a3/src/skills/lifecycle/source-install-metadata.ts), type in `workspace-types.ts`):

```ts
export type SkillSourceOrigin = {
  version: 1;
  source: "path" | "git";
  spec: string;
  slug: string;
  installedAt: number;
  git?: { url: string; ref?: string; commit?: string; resolvedAt: string };
};
```

Installing from git or a path over a ClawHub-tracked skill deletes that skill's `.clawhub` directory and removes its lock entry (same file). No content hash is recorded for these installs, so local edits to a git-installed skill are not detectable.

### What ClawHub's own repo uses

ClawHub's repository commits a root [`skills-lock.json`](https://github.com/openclaw/clawhub/blob/3635eb2fcc3b80cc3d6ee6a6455cb493d998c4d1/skills-lock.json) for the skills its own contributors and agents use. This is a real excerpt:

```json
{
  "version": 1,
  "skills": {
    "axiom-alerting": {
      "source": "axiomhq/skills",
      "sourceType": "github",
      "skillPath": "skills/axiom-alerting/SKILL.md",
      "computedHash": "e7946a75c50093b72be697b9459a007e14ccc2d07b6544e5dc383e0a028abefa"
    },
    "openclaw-brand": {
      "source": "openclaw/carapace",
      "sourceType": "github",
      "skillPath": "openclaw-brand/SKILL.md",
      "computedHash": "d4bf50bb9d512dbfbcd4abde7a75d8399b0187ca9486dd7854844aab0cd02e6a"
    }
  }
}
```

It records repo, path inside the repo and a content hash, and no commit. This is the format of the `skills` CLI (`npx skills add`), which kinby's own `skills-lock.json` and `skills-lock.coder.json` already follow. That attribution rests on the matching shape; the ClawHub repo does not name the tool in the file (**unverified**).

## Update, local edits, force, diff, removal

**Discovery.** There is no background update check for skills in the docs read. The user runs `openclaw skills update @owner/<slug>` or `--all`, per workspace, per `--agent <id>`, or `--global` ([O:docs/cli/skills.md](https://github.com/openclaw/openclaw/blob/73f9f4e54257784cac122cae7466a44cc8f098a3/docs/cli/skills.md)). An "update available" indicator in the Control UI was not found in the docs (**unverified**).

**Local-edit detection in OpenClaw.** Before downloading, update reads the recorded `skillFile.sha256` and `fileTreeSha256`, recomputes both from disk, and refuses on a mismatch with code `force_required` and the message "Updating replaces the installed skill directory." The verified digests are carried into the install transaction and checked again at replacement time, so an edit made during the download also blocks ([O:src/skills/lifecycle/clawhub.ts](https://github.com/openclaw/openclaw/blob/73f9f4e54257784cac122cae7466a44cc8f098a3/src/skills/lifecycle/clawhub.ts); `checkClawHubSkillPlanAtPath` in `skill-tree-digest.ts`).

**`--force`.** The docs: "Replaces a tracked skill even when its installed files no longer match recorded install digests. Without it, updates preserve local changes. Pre-digest installs require one forced update before later updates can be verified. Force those skills individually; `--all --force` also replaces skills with detected local edits." On `install`, `--force` "overwrites an existing workspace skill folder for the same slug". It does not override a trust block or the install policy.

**Local-edit detection in the `clawhub` CLI.** It "computes fingerprint from local files", asks the registry which version matches, and "if fingerprint does not match: refuses by default; overwrites with `--force` (or prompt, if interactive)" ([C:docs/cli.md](https://github.com/openclaw/clawhub/blob/3635eb2fcc3b80cc3d6ee6a6455cb493d998c4d1/docs/cli.md), `update`). The printed line is `<slug>: local changes (no match). Use --force to overwrite.`

**What happens to an edited skill.** It stays as it is, or it is replaced whole. Neither tool merges. Replacement is transactional: the old directory is moved to a `.backup-` sibling and restored on failure.

**Diff.** Neither CLI has a diff command. The diff lives on the registry website: a card on the skill page that compares any file between two versions, default `previous` against `latest` by semver ([C:specs/diffing.md](https://github.com/openclaw/clawhub/blob/3635eb2fcc3b80cc3d6ee6a6455cb493d998c4d1/specs/diffing.md)). `clawhub inspect @owner/slug --files` and `--file <path>` fetch a version's files without installing, so a local diff is possible by hand. A diff of local edits against the installed version does not exist. The Skill Workshop keeps its own version history for agent-authored skills, and the managed library keeps revisions; those are separate from registry installs.

**Removal.** OpenClaw has no `skills uninstall`. The docs send users to the standalone CLI: `clawhub --workdir <root> uninstall @owner/my-skill`, which deletes the skill directory and its lock entry after a confirmation. `<root>` is the agent workspace, or `~/.openclaw` for a `--global` install. The file watcher picks the removal up on the next turn ([O:docs/cli/skills.md](https://github.com/openclaw/openclaw/blob/73f9f4e54257784cac122cae7466a44cc8f098a3/docs/cli/skills.md), "Remove a ClawHub skill"). Disabling without removing is `skills.entries.<key>.enabled: false`, or leaving the name out of the agent's allowlist.

## Trust

**At publish (registry side).**

- Skill publishing requires a GitHub account at least 14 days old, checked by immutable numeric GitHub ID ([C:specs/security-moderation.md](https://github.com/openclaw/clawhub/blob/3635eb2fcc3b80cc3d6ee6a6455cb493d998c4d1/specs/security-moderation.md), "Upload gate").
- Each release gets an audit with a status (`Pass`, `Review`, `Warn`, `Malicious`, `Pending`, `Error`) and a risk level (`Low`, `Medium`, `High`). The audit combines SkillSpector, Tencent's A.I.G scanner and ClawScan, ClawHub's own LLM-based review, which uses the OWASP Agentic Skills Top 10 as its lens ([C:docs/security-audits.md](https://github.com/openclaw/clawhub/blob/3635eb2fcc3b80cc3d6ee6a6455cb493d998c4d1/docs/security-audits.md)).
- Skills containing `.pyc`, `.pyo` or `.pyd` files are rejected (same page).
- Users report listings; moderators hide, quarantine or ban ([C:docs/moderation.md](https://github.com/openclaw/clawhub/blob/3635eb2fcc3b80cc3d6ee6a6455cb493d998c4d1/docs/moderation.md)).

**VirusTotal, then and now.** OpenClaw announced the VirusTotal partnership on 2026-02-07: every published skill hashed and scanned, malicious ones "instantly blocked from download", and "all active skills are re-scanned daily" ([openclaw.ai/blog/virustotal-partnership](https://openclaw.ai/blog/virustotal-partnership), read through a summarising fetch, so the quotes are second-hand). The current spec has walked that back: "VirusTotal is telemetry only … VT alone must never hide, block, or set malicious/suspicious public status", and "All-active daily VirusTotal sweeps are disabled" ([C:specs/security-moderation.md](https://github.com/openclaw/clawhub/blob/3635eb2fcc3b80cc3d6ee6a6455cb493d998c4d1/specs/security-moderation.md)). The blocking verdict now comes from ClawScan.

**The malicious-skill incidents.** Neither repository narrates them. OpenClaw's threat model lists "Attacker publishes a malicious skill to ClawHub" with mitigations "GitHub account age verification, static pattern/AST-adjacent scanning, LLM-based agentic risk review, VirusTotal scanning" ([O:docs/security/THREAT-MODEL-ATLAS/persistence.md](https://github.com/openclaw/openclaw/blob/73f9f4e54257784cac122cae7466a44cc8f098a3/docs/security/THREAT-MODEL-ATLAS/persistence.md)). The widely reported early-2026 campaign of several hundred malicious ClawHub skills (reported as "ClawHavoc"), and the claim that the scanning pipeline was a response to it, come from secondary reporting and are **unverified** here.

**At install (client side).**

- "Community ClawHub skill installs and updates check trust before downloading." Malicious or blocked releases are refused. `Review` outcomes print the audit link and continue. Official publishers and bundled skills skip the check ([O:docs/cli/skills.md](https://github.com/openclaw/openclaw/blob/73f9f4e54257784cac122cae7466a44cc8f098a3/docs/cli/skills.md), "Release trust").
- A pending scan does not block by default for hosted skills: "a pending or stale scan can allow installation with a warning" ([O:docs/start/why-openclaw.md](https://github.com/openclaw/openclaw/blob/73f9f4e54257784cac122cae7466a44cc8f098a3/docs/start/why-openclaw.md)). For GitHub-backed skills a pending scan does block, and `--force-install` is the override.
- `openclaw skills verify @owner/<slug>` fetches the `clawhub.skill.verify.v1` envelope for the installed version. It "does not hash current local files" (same page).
- **Operator install policy.** `security.installPolicy` names a local executable. OpenClaw stages the source, sends one JSON object on stdin (`targetType`, `targetName`, `sourcePath`, `source`, `origin`, `request`) and expects `allow`, `warn` or `block`. It covers ClawHub, git, local, upload and update paths, for both skills and plugins. A `warn` needs the operator to type the skill name. A timeout, bad JSON or non-zero exit blocks. "`--force` does not approve policy warnings" ([O:docs/tools/skills-config.md](https://github.com/openclaw/openclaw/blob/73f9f4e54257784cac122cae7466a44cc8f098a3/docs/tools/skills-config.md), "Operator Install Policy").
- Path containment: a skill root's realpath must stay inside its configured root unless `skills.load.allowSymlinkTargets` trusts the target.

**Gating by environment.** `metadata.openclaw.requires` in the frontmatter filters skills at load time: `bins`, `anyBins`, `env`, `config`, plus `os`. `always: true` skips all but `os`. A skill can be in inventory, not ready (a requirement is missing) and not visible (allowlist), and `openclaw skills check` reports the three separately ([O:docs/tools/skills.md](https://github.com/openclaw/openclaw/blob/73f9f4e54257784cac122cae7466a44cc8f098a3/docs/tools/skills.md), "Gating"). `metadata.openclaw.install` lists installers (brew, node, go, uv, download with optional `sha256`) for the missing binaries.

**Sandbox.** `requires.bins` is checked on the host, so the binary must also exist in the container. `skills.entries.*.env` and `.apiKey` are injected into the host process for the turn and never reach the sandbox. Sandboxed runs "read the materialized copies, not the original host paths", and the agent allowlist also governs what is synced into the sandbox (same page, "Sandboxing notes" and "Loading order").

## Plugins, tools and MCP servers

These do not share the skills mechanism.

**Plugins** carry tools, channels, providers and hooks. `openclaw plugins install` accepts `clawhub:<package>[@version]`, `npm:<package>[@version]`, bare npm names, `npm-pack:<path.tgz>`, `git:github.com/<owner>/<repo>[@ref]`, local paths (`-l` links instead of copying) and marketplace entries ([O:docs/cli/plugins/install.md](https://github.com/openclaw/openclaw/blob/73f9f4e54257784cac122cae7466a44cc8f098a3/docs/cli/plugins/install.md)).

- **Pinning:** `--pin` "applies to npm installs only and records the resolved exact `<name>@<version>`". Git pins by putting the ref in the spec. An unversioned ClawHub install records an unversioned spec so updates follow new releases; `clawhub:pkg@1.2.3` stays pinned.
- **Ledger:** install records live in the shared SQLite state database, in the `config_machine_state` value keyed `plugins.installedIndex`, and hold source metadata, artifact kind, npm integrity, shasum and digest. The old `plugins.installs` config key is retired ([O:docs/cli/plugins/list.md](https://github.com/openclaw/openclaw/blob/73f9f4e54257784cac122cae7466a44cc8f098a3/docs/cli/plugins/list.md), "Plugin index").
- **Update:** `openclaw plugins update <id>|--all [--dry-run]`. If the fetched artifact's hash differs from the stored integrity for the same version, that is "npm artifact drift": the interactive command prints both hashes and asks, and non-interactive helpers refuse ([O:docs/cli/plugins/uninstall-and-update.md](https://github.com/openclaw/openclaw/blob/73f9f4e54257784cac122cae7466a44cc8f098a3/docs/cli/plugins/uninstall-and-update.md)).
- **Trust:** ClawHub and the official catalog are trusted sources. Any other source warns and needs `--force` when non-interactive. Capabilities need separate consent (`--accept-capabilities`). Removal is `openclaw plugins uninstall <ids...>`.
- Plugins are gateway-wide, gated by `plugins.allow`, `plugins.deny` and `plugins.entries.<id>`. A plugin can ship skills by listing directories in `openclaw.plugin.json`; those load at the lowest precedence tier.

**MCP servers** are config. `openclaw mcp add|set|configure|unset` edit `mcp.servers` entries in OpenClaw config, with no lock file and no registry install step ([O:docs/cli/mcp.md](https://github.com/openclaw/openclaw/blob/73f9f4e54257784cac122cae7466a44cc8f098a3/docs/cli/mcp.md)). A second MCP registry, mcporter's `~/.openclaw/skills/config/mcporter.json`, is used by skills and is separate.

**Claws** are the piece closest to kinby's "an instance picks from the library at creation". A Claw is "a versioned package that describes one complete OpenClaw agent and the reusable resources it needs": a `CLAW.md` whose frontmatter lists `packages` (`kind: skill`, `source: clawhub`, `ref`, `version`), `mcpServers`, `cronJobs` and workspace files, and whose body becomes the agent's `SOUL.md`. "Dependency versions must be exact", and publication rejects "floating skill/plugin versions and resolved MCP credentials". The consumer runs `openclaw claws add --dry-run` to preview, then consents. It is experimental and gated on both sides ([C:docs/claws.md](https://github.com/openclaw/clawhub/blob/3635eb2fcc3b80cc3d6ee6a6455cb493d998c4d1/docs/claws.md)).

## Onboarding

`openclaw onboard` has two shapes ([O:docs/start/wizard.md](https://github.com/openclaw/openclaw/blob/73f9f4e54257784cac122cae7466a44cc8f098a3/docs/start/wizard.md)).

**Guided (default on a fresh install).** Security pointer, then Quick start or Custom setup. Detect available AI access, choose one connection, verify it with a real completion. Choose one agent or a small team. Save, prepare the workspace, start the Gateway, open the dashboard. Quick start asks nothing about skills. Channels, skills and web search come afterwards, conversationally (`configure skills`, `open channel wizard for <channel>`).

**Classic (`openclaw onboard --classic`), in order:**

1. Workspace
2. Model and auth
3. Gateway
4. Channels
5. Web search
6. Skills
7. Daemon
8. Health check

The Skills step is not a catalogue picker. It "reads available skills and checks requirements", lets the user choose a node manager, and "installs optional dependencies for trusted bundled skills" ([O:docs/start/wizard-cli-reference.md](https://github.com/openclaw/openclaw/blob/73f9f4e54257784cac122cae7466a44cc8f098a3/docs/start/wizard-cli-reference.md)). On macOS the classic wizard also scans installed applications and asks the model to recommend matching plugins or skills from the official catalogs and ClawHub, with only official-catalog matches selected by default (same page).

**Adding a later agent.** `openclaw agents add <name>` asks for name, workspace, auth, channels and bindings. It has no skills step. A new agent sees every bundled and shared managed skill unless an allowlist narrows it, so the default is "everything visible", and picking means restricting.

## The standard it follows

"OpenClaw follows the [AgentSkills](https://agentskills.io) spec" ([O:docs/tools/skills.md](https://github.com/openclaw/openclaw/blob/73f9f4e54257784cac122cae7466a44cc8f098a3/docs/tools/skills.md), "SKILL.md format"). A skill is a folder with a `SKILL.md` that has YAML frontmatter with at least `name` and `description`. ClawHub adds that "for portable Agent Skills, `name` should match the parent directory and use 1–64 lowercase letters, numbers, or hyphens" ([C:docs/skill-format.md](https://github.com/openclaw/clawhub/blob/3635eb2fcc3b80cc3d6ee6a6455cb493d998c4d1/docs/skill-format.md)).

OpenClaw's additions sit beside or under the standard keys:

- Top level: `homepage`, `user-invocable`, `disable-model-invocation`, `command-dispatch`, `command-tool`, `command-arg-mode`, and `version` on ClawHub.
- Under `metadata.openclaw`: `requires`, `primaryEnv`, `os`, `always`, `install`, `skillKey`, `emoji`, `homepage`.

The Agent Skills spec covers the folder and the frontmatter. It does not define installation, a lock file or an update protocol. Everything in the pinning and update sections above is OpenClaw's own.

## What this means for kinby

Steal:

- **Copy on pick, with an origin record inside the copy and a lock at the root.** It makes an agent's skills self-contained and makes "what does this agent run" answerable from its files. kinby's factory template sync already works this way.
- **A tree digest recorded at install.** It is the whole local-edit story: recompute, compare, refuse without force. Record it for git sources too, which OpenClaw does not.
- **Commit as the version for git sources**, with the ref kept beside it (`git: {url, ref, commit, resolvedAt}`), and the scan or review verdict bound to the content hash.
- **Location and visibility as separate controls.** One shared store plus a per-agent list of names is enough for a single-user hub.
- **The three-way status: inventory, readiness, visibility.** It turns "why does my agent not see this skill" into one command.
- **Shipped skills as their own tier**, versioned with the release and never tracked, with a subset reserved for the home agent (Custodian skills).
- **One install-policy hook** that sees the staged files before anything is committed, shared by every source and every kind of pickable thing.
- **Exact versions in an agent template** (the Claw manifest), so creating an instance from a template is reproducible.
- **`SKILL.md` as-is**, with kinby's own fields under `metadata.kinby`.

Do not steal:

- **A public registry with semver, owners, tags and scanning.** It is most of ClawHub's code and exists to make strangers' uploads safe. A single-user hub adding from git at a commit does not need it.
- **Two lock dialects for one path.** OpenClaw and the `clawhub` CLI write different fields to the same files, and pinning exists in only one of them.
- **Git installs that cannot update and cannot sit in a subfolder.** kinby's main source will be git repos holding several skills, so source layout must be `repo + path + commit`, which is what `skills-lock.json` and ClawHub's GitHub-backed model already record.
- **Removal through a second CLI.** Install, update and remove belong in one place.
- **All-or-nothing replacement of an edited skill** as the only option. With git as the source, a three-way view (base commit, local, new commit) is cheap to offer.
- **Three mechanisms for skills, plugins and MCP servers.** kinby wants one library for all pickable things; OpenClaw shows what it costs to grow them separately.
- **"Everything visible by default".** For a library that agents pick from, an agent should start with what its template names.

Open questions this note does not settle:

- Whether to adopt the `skills-lock.json` shape for the hub library (kinby already has two such files) or the `.clawhub` pair. The first has no commit field; the second has no path-in-repo field. Neither fits unchanged.
- Whether a pick should pin per agent (OpenClaw's copy model) or per session (OpenClaw's managed-library model). The managed library's immutable revisions keyed by content hash are the cleaner reference design if kinby ever wants picks to be references.
