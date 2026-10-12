# Hermes Agent: how skills get installed, pinned, updated and trusted

Date: 2026-10-12.

Question: kinby is designing a hub-level library of pickable things (skills first). How does Hermes Agent handle the same problem: what sources a skill can come from, where it lands, what is recorded, how updates and local edits are treated, and what is refused?

Source: [`NousResearch/hermes-agent@8a7a5a9`](https://github.com/NousResearch/hermes-agent/tree/8a7a5a905fc66ed82b922587a8e3c011e819dbb5), read from a clone. `H:path` below means `https://github.com/NousResearch/hermes-agent/blob/8a7a5a905fc66ed82b922587a8e3c011e819dbb5/path`. Claims cite code where code was read and docs where only the doc was read. Nothing was run, so every behavior here is "what the code says", not "what was observed".

This note builds on `docs/research/self-extending-agents.md` (branch `research/self-extending-agents`), which covers how Hermes writes skills itself (`skill_manage`, the write-approval gate, the curator). None of that is repeated.

## Short answer

1. **A pick is a copy.** Every skill, whatever its origin, is copied into the profile's own `~/.hermes/skills/`. There is no shared store that agents reference. Sharing is a separate, opt-in mechanism (`skills.external_dirs`).
2. **Three origins, told apart by two sidecar files.** A skill named in `skills/.hub/lock.json` is hub-installed. A skill named in `skills/.bundled_manifest` is bundled. Anything else is local (written by the agent or the user).
3. **The lock is Hermes's own format.** It records source, identifier, a content hash, the file list, the scan verdict and the scan evidence. It is not the skills.sh `skills-lock.json`.
4. **Skills are not pinned to a commit.** A GitHub skill is always fetched from the default branch. The tree sha is recorded, but only so that the update check can skip a download. Plugins, by contrast, can be pinned to a 40-hex commit.
5. **Update is hash-compare then replace.** `hermes skills check` compares the recorded hash with a hash of the upstream bundle. `hermes skills update` deletes the directory and reinstalls. A locally edited skill is skipped unless `--force`. There is no merge and no diff for hub skills.
6. **Trust is a table of source tier by scan verdict.** A regex scanner gives `safe`, `caution` or `dangerous`. `dangerous` from a third party is never installable, even with `--force`.
7. **Skills, plugins, MCP servers and whole agents each have their own install path** and their own record format. Nothing unifies them.

## Sources a skill can be installed from

One CLI entry point, `hermes skills install <identifier>` (also `/skills install` in chat), routes to a source adapter. Each adapter implements `search`, `fetch`, `inspect`, and optionally `current_revision` ([`H:tools/skills_hub_models.py`](https://github.com/NousResearch/hermes-agent/blob/8a7a5a905fc66ed82b922587a8e3c011e819dbb5/tools/skills_hub_models.py), class `SkillSource`).

| Source id | Identifier | What it is |
|---|---|---|
| `official` | `official/security/1password` | `optional-skills/` in the Hermes repo. Read from the local checkout, with a live-repo fallback. Trust `builtin`. |
| `github` | `owner/repo/path/to/skill-dir` | Any GitHub repo path, plus taps. |
| `skills-sh` | `skills-sh/vercel-labs/json-render/json-render-react` | skills.sh is used for discovery only; the content is fetched from the underlying GitHub repo. |
| `well-known` | `well-known:https://mintlify.com/docs/.well-known/skills/mintlify` | A site that serves `/.well-known/skills/index.json`. |
| `url` | `https://example.com/my-skill/SKILL.md` | A bare `SKILL.md` URL. |
| `clawhub`, `lobehub`, `browse-sh` | source-specific | Third-party marketplaces. |

Sources: the table in [`H:website/docs/user-guide/features/skills.md`](https://github.com/NousResearch/hermes-agent/blob/8a7a5a905fc66ed82b922587a8e3c011e819dbb5/website/docs/user-guide/features/skills.md) ("Supported hub sources"), and the adapters in `tools/skills_hub_official.py`, `tools/skills_hub_github.py`, `tools/skills_hub_skillssh.py`, `tools/skills_hub_sources.py`, `tools/skills_hub_clawhub.py`.

Search is answered from a centralized index, `https://hermes-agent.nousresearch.com/docs/api/skills-index.json`, built by CI and cached for an hour, with a live fallback to the registries on a miss ([`H:tools/skills_hub_search.py`](https://github.com/NousResearch/hermes-agent/blob/8a7a5a905fc66ed82b922587a8e3c011e819dbb5/tools/skills_hub_search.py), [`H:tools/skills_hub.py`](https://github.com/NousResearch/hermes-agent/blob/8a7a5a905fc66ed82b922587a8e3c011e819dbb5/tools/skills_hub.py) `INDEX_CACHE_TTL = 3600`).

### Taps and the layout a source repo must have

A tap is a GitHub repo registered as a search source. It is stored in `~/.hermes/skills/.hub/taps.json`:

```json
{
  "taps": [
    {"repo": "my-org/platform-docs", "path": "internal/skills/"}
  ]
}
```

`hermes skills tap add owner/repo` writes `path: "skills/"`; a different path means editing the file by hand ([`H:tools/skills_hub.py`](https://github.com/NousResearch/hermes-agent/blob/8a7a5a905fc66ed82b922587a8e3c011e819dbb5/tools/skills_hub.py) `TapsManager`, and the skills doc, "Non-default paths"). Seven default taps are hard-coded: `openai/skills`, `anthropics/skills`, `huggingface/skills`, `NVIDIA/skills`, `garrytan/gstack` and two science repos ([`H:tools/skills_hub_github.py`](https://github.com/NousResearch/hermes-agent/blob/8a7a5a905fc66ed82b922587a8e3c011e819dbb5/tools/skills_hub_github.py) `DEFAULT_TAPS`).

Layout rules, from the skills doc ("Publishing a custom skill tap"):

- One directory per skill directly under the tap path. Discovery walks one level only, so a nested repo needs one tap entry per inner path.
- The directory name is the install slug. Names starting with `.` or `_` are skipped.
- Each directory holds a `SKILL.md` with `name` and `description` frontmatter.
- Support files go in `references/`, `templates/`, `scripts/`, `assets/`, `examples/`.
- A repo-root `SKILL.md` (single-skill repo) is also accepted (`_skill_file_path` in `skills_hub_github.py`).
- An optional repo-root `skills.sh.json` (the skills.sh schema) supplies category groupings.

A tap is only needed for search. `hermes skills install owner/repo/skills/my-workflow` works on any repo without one.

No git is involved for skills. The GitHub adapter resolves the default branch's tree through the REST API, then downloads each file from `raw.githubusercontent.com` at that tree sha ([`H:tools/skills_hub_github.py`](https://github.com/NousResearch/hermes-agent/blob/8a7a5a905fc66ed82b922587a8e3c011e819dbb5/tools/skills_hub_github.py) `fetch`, `_get_repo_tree`, `_fetch_file_bytes`). Auth is `GITHUB_TOKEN`, then the `gh` CLI, then a GitHub App, then anonymous (`GitHubAuth`).

For third-party GitHub and URL installs, the bundle is `SKILL.md` plus the files it references under the five allowlisted directories. Unreferenced repo files are not copied (skills doc, "Skill Directory Structure"; `_referenced_support_paths` in `skills_hub_models.py`).

## Where skills live

```text
~/.hermes/skills/                  # one per profile
├── mlops/axolotl/SKILL.md         # category dir / skill dir
├── .hub/
│   ├── lock.json                  # hub-installed skills
│   ├── taps.json
│   ├── quarantine/                # staging before the scan
│   ├── index-cache/
│   └── audit.log
└── .bundled_manifest              # bundled skills
```

Source: skills doc, "Skill Directory Structure", and the path resolvers at the top of [`H:tools/skills_hub.py`](https://github.com/NousResearch/hermes-agent/blob/8a7a5a905fc66ed82b922587a8e3c011e819dbb5/tools/skills_hub.py).

**Per agent, not shared.** A profile is a separate `HERMES_HOME`, and each has its own `skills/`, lock and manifest ([`H:website/docs/user-guide/profiles.md`](https://github.com/NousResearch/hermes-agent/blob/8a7a5a905fc66ed82b922587a8e3c011e819dbb5/website/docs/user-guide/profiles.md)). The Hermes code is shared, and `hermes update` syncs newly bundled skills into every profile. `hermes profile create --clone` copies the source profile's skills once; the two profiles are independent afterwards.

**Sharing is by extra scan directories.** `skills.external_dirs` in `config.yaml` adds read locations (for example `~/.agents/skills`). Precedence on a name clash is `project → local → skills.create_dir → external_dirs`. An external directory is not write-protected: the agent's `skill_manage` edits a skill wherever it was found (skills doc, "External Skill Directories").

**Telling the three origins apart.** `hermes skills list` labels a skill `hub` when its name is in `lock.json`, `builtin` when its name is in `.bundled_manifest`, and `local` otherwise ([`H:hermes_cli/skills_hub.py`](https://github.com/NousResearch/hermes-agent/blob/8a7a5a905fc66ed82b922587a8e3c011e819dbb5/hermes_cli/skills_hub.py) `do_list`). There is no marker inside the skill directory itself. The curator uses the same split: hub-installed skills are off-limits to it ([`H:website/docs/user-guide/features/curator.md`](https://github.com/NousResearch/hermes-agent/blob/8a7a5a905fc66ed82b922587a8e3c011e819dbb5/website/docs/user-guide/features/curator.md)).

## The lock file

`~/.hermes/skills/.hub/lock.json`. The writer is `HubLockFile.record_install` in [`H:tools/skills_hub.py`](https://github.com/NousResearch/hermes-agent/blob/8a7a5a905fc66ed82b922587a8e3c011e819dbb5/tools/skills_hub.py):

```python
data["installed"][safe_name] = {
    "source": source,
    "identifier": identifier,
    "trust_level": trust_level,
    "scan_verdict": scan_verdict,
    "content_hash": skill_hash,
    "install_path": safe_install_path,
    "files": files,
    "metadata": metadata or {},
    "scan_provenance": scan_provenance or {},
    "installed_at": now,
    "updated_at": now,
}
```

The repo ships no sample `lock.json`. The example below is assembled from that writer, the GitHub adapter's `metadata`, and `scan_skill_cached`. The field names are exact; the values are illustrative.

```json
{
  "version": 1,
  "installed": {
    "k8s": {
      "source": "github",
      "identifier": "openai/skills/skills/.curated/k8s",
      "trust_level": "trusted",
      "scan_verdict": "safe",
      "content_hash": "sha256:1f3a9c0b7d2e4a55",
      "install_path": "k8s",
      "files": ["SKILL.md", "references/rollout.md"],
      "metadata": {
        "source_url": "https://github.com/openai/skills/tree/<tree-sha>/skills/.curated/k8s",
        "source_revision": "<tree-sha>"
      },
      "scan_provenance": {
        "bundle_hash": "sha256:<64 hex>",
        "scanner_version": "skills-guard-v10",
        "source": "openai/skills/skills/.curated/k8s",
        "source_url": "https://github.com/openai/skills/tree/<tree-sha>/skills/.curated/k8s",
        "verdict": "safe",
        "trust_level": "trusted",
        "findings": [],
        "rules": [],
        "scanned_at": "2026-10-12T09:00:00+00:00",
        "summary": "...",
        "fresh": true
      },
      "installed_at": "2026-10-12T09:00:00+00:00",
      "updated_at": "2026-10-12T09:00:00+00:00"
    }
  }
}
```

Field notes:

- **The key is the bare skill name.** Names are not namespaced by registry. The update code works around this by fetching only from the adapter named in `source` (comment in `check_for_skill_updates`, [`H:tools/skills_hub_install.py`](https://github.com/NousResearch/hermes-agent/blob/8a7a5a905fc66ed82b922587a8e3c011e819dbb5/tools/skills_hub_install.py)).
- **`content_hash`** is `sha256:` plus the first 16 hex digits of a SHA-256 over every file, sorted by POSIX relative path, each fed as `path`, a NUL byte, then the bytes. The same function runs over the on-disk directory (`content_hash` in [`H:tools/skills_guard.py`](https://github.com/NousResearch/hermes-agent/blob/8a7a5a905fc66ed82b922587a8e3c011e819dbb5/tools/skills_guard.py)) and over a fetched in-memory bundle (`bundle_content_hash` in `skills_hub_install.py`). The two must stay symmetric; a Windows path-separator mismatch once made every skill report an update forever (issue #62310, cited in both docstrings).
- **`install_path`** is relative to `skills/` and is validated on write and on read, because uninstall runs `rmtree` on it (`_resolve_lock_install_path`).
- **`metadata.source_revision`** exists only for the GitHub adapter. It is the tree sha of the default branch at fetch time, left empty if any file failed to download. Other adapters record their own keys (`index_url`, `endpoint`, `slug`).
- **`scan_provenance`** is the scanner's attestation: full bundle hash, scanner version, findings, rule ids, and whether the result was fresh or served from cache (`scan_skill_cached`).
- **`installed_at` and `updated_at` are both set to now on every write**, so an update loses the original install time. This is read from the writer above.

`hermes skills snapshot export` writes a thinner portable file: `hermes_version`, `exported_at`, `skills` (name, source, identifier, category) and `taps`. It carries no hash and no revision, so an import reinstalls whatever is upstream that day (`do_snapshot_export` in [`H:hermes_cli/skills_hub.py`](https://github.com/NousResearch/hermes-agent/blob/8a7a5a905fc66ed82b922587a8e3c011e819dbb5/hermes_cli/skills_hub.py)).

### The bundled manifest

`~/.hermes/skills/.bundled_manifest` is a text file, one `name:hash` line per bundled skill, sorted. The hash is an MD5 over relative paths and file bytes, excluding runtime caches such as `__pycache__` (`_read_manifest`, `_write_manifest`, `_dir_hash` in [`H:tools/skills_sync.py`](https://github.com/NousResearch/hermes-agent/blob/8a7a5a905fc66ed82b922587a8e3c011e819dbb5/tools/skills_sync.py)). It records the **origin hash**: the bundled content at the time it was last copied in.

So Hermes keeps two provenance files with two hash algorithms for what is the same question ("is this copy still what we installed?").

## Install flow

From `_install_skill` in [`H:hermes_cli/skills_hub.py`](https://github.com/NousResearch/hermes-agent/blob/8a7a5a905fc66ed82b922587a8e3c011e819dbb5/hermes_cli/skills_hub.py) and `install_from_quarantine` in `skills_hub_install.py`:

1. Resolve the identifier to an adapter and fetch the bundle into memory.
2. If the name is already in the lock, stop unless `--force`.
3. Write the bundle to `skills/.hub/quarantine/<name>/`. Every relative path is validated first.
4. Scan the quarantined directory. Apply the install policy. A blocked bundle is not installed.
5. Optionally run the advisory NVIDIA SkillEvaluator scan, which prints findings and never blocks.
6. Ask the user to confirm (skipped with `--force` and in the TUI).
7. Refuse any symlink in the bundle, then move the directory to `skills/<category>/<name>/`.
8. Hash the installed directory, write the lock entry, append a line to `audit.log`.

The audit line is space-separated: timestamp, action, skill name, `source:trust_level`, verdict, extra (`append_audit_log`).

## Update, local edits, removal

**Check.** `hermes skills check [name]` walks the lock. For each entry it reports one of `up_to_date`, `update_available`, `unavailable` (the source adapter could not fetch it), `orphaned` (the directory is gone) or `invalid_install` (unsafe recorded path). For GitHub, if the repo's current tree sha equals the recorded `source_revision`, the skill is `up_to_date` with no download. Otherwise the bundle is fetched and its hash compared with `content_hash` (`check_for_skill_updates`). The output is a table of name, source and status. No diff of the upstream change is shown.

**Apply.** `hermes skills update [name] [--force]` reinstalls each `update_available` skill through the normal install path, pinned to the recorded source so a same-named skill in another registry cannot take its place (`do_update`). The reinstall deletes the directory and moves the new one in. There is no three-way merge.

**Locally edited skills.** Before updating, the on-disk directory is rehashed. If it no longer matches `content_hash`, the skill is skipped with "you have local edits", and `hermes skills update <name> --force` is the only way to overwrite it (`_has_local_edits`, `do_update`). Nothing shows the user what they changed.

**One consequence worth knowing.** `do_update` calls `do_install(..., force=True)`, and `force` is passed straight to `should_allow_install`. So an update skips the confirmation prompt and also overrides a `caution` block on a community source. A `dangerous` verdict still blocks. This is read from the code path, not tested.

**Bundled skills** follow the same rule with the other file. On each sync (`hermes update`, or install), a bundled skill is replaced only if the user's copy still matches its origin hash. An edited copy is left alone indefinitely, a deleted one is not re-added, and a new bundled skill is copied in ([`H:tools/skills_sync.py`](https://github.com/NousResearch/hermes-agent/blob/8a7a5a905fc66ed82b922587a8e3c011e819dbb5/tools/skills_sync.py) module docstring). Bundled skills are the only ones with a diff: `hermes skills list-modified` and `hermes skills diff <name>` compare the user's copy with the stock version, and `hermes skills reset <name> [--restore]` re-baselines or restores it ([`H:tools/skills_sync_bundled_ops.py`](https://github.com/NousResearch/hermes-agent/blob/8a7a5a905fc66ed82b922587a8e3c011e819dbb5/tools/skills_sync_bundled_ops.py)). A `.no-bundled-skills` marker in the profile stops seeding (`hermes skills opt-out`, `hermes profile create --no-skills`).

**Remove.** `hermes skills uninstall <name>` works only for names in the lock. It deletes the directory, drops the lock entry and logs `UNINSTALL`. A bundled or local skill gets "is not a hub-installed skill (may be a builtin)" (`uninstall_skill`). Local skills are removed by the agent's `skill_manage` delete or by hand.

## Trust

Everything here is in [`H:tools/skills_guard.py`](https://github.com/NousResearch/hermes-agent/blob/8a7a5a905fc66ed82b922587a8e3c011e819dbb5/tools/skills_guard.py).

**Trust level comes from the source.** `official` maps to `builtin`. A GitHub path inside one of four hard-coded repos (`TRUSTED_REPOS`: `openai/skills`, `anthropics/skills`, `huggingface/skills`, `NVIDIA/skills`) is `trusted`. Everything else is `community`, including every user-added tap. Raising a repo to `trusted` takes a pull request to Hermes itself.

**Verdict comes from the scan.** The scanner is a set of regexes over every text file, plus structural checks. Any `critical` finding gives `dangerous`, any `high` gives `caution`, otherwise `safe` (`_determine_verdict`).

**Policy is the product of the two:**

```text
INSTALL_POLICY = {
    #                  safe      caution    dangerous
    "builtin":       ("allow",  "allow",   "allow"),
    "trusted":       ("allow",  "allow",   "block"),
    "community":     ("allow",  "block",   "block"),
    "agent-created": ("allow",  "allow",   "ask"),
}
```

`--force` overrides a block except `dangerous` on `trusted` or `community` (`should_allow_install`). The `agent-created` row applies only when `skills.guard_agent_created` is on.

**What is refused outright:**

- A binary by extension (`.exe`, `.dll`, `.so`, `.dylib`, `.bin`, `.msi`, `.dmg` and others): a `critical` finding, so `dangerous`.
- A symlink that points outside the skill: `critical`. Separately, the installer rejects any symlink at all in a hub bundle.
- A relative path that escapes the skill directory, in the bundle or in `SKILL.md` references (the fetch fails closed).
- Softer limits that only add findings: more than 50 files, a file over 256 KB, an executable bit on a non-script.

**Scripts versus markdown.** Scripts are not blocked and not sandboxed at install time. They are scanned as text with the same patterns as markdown (`SCANNABLE_EXTENSIONS` includes `.py`, `.sh`, `.js`). The scanner's own docstring names the gap: it cannot tie a file-write API call to its destination. How scripts are constrained when the agent later runs them is a terminal and sandbox question that this note did not trace. **Unverified.**

**Quarantine** is the staging directory `skills/.hub/quarantine/`, not a holding area a user reviews. A blocked bundle is discarded.

**Fetching is guarded.** Hub HTTP goes through an SSRF-checked client, with redirects followed by hand and re-checked at each hop, capped at five ([`H:tools/skills_hub.py`](https://github.com/NousResearch/hermes-agent/blob/8a7a5a905fc66ed82b922587a8e3c011e819dbb5/tools/skills_hub.py) `_guarded_http_get`).

**Project skills** (`<repo>/.hermes/skills/`, `<repo>/.agents/skills/`) are not loaded until the user runs `hermes skills trust`, and each is rescanned whenever its content hash changes (skills doc, "Project-Local Skills").

`hermes skills audit` rescans installed hub skills.

## What else the mechanism covers

Nothing. Each kind of thing has its own path.

| Thing | Install | Pin and record | Update |
|---|---|---|---|
| Skill | `hermes skills install` | `skills/.hub/lock.json`: content hash, no commit pin | `check` / `update`, hash compare, replace |
| Plugin (Python code) | `hermes plugins install <catalog-name \| owner/repo> [--ref <40-hex>]`, a real `git clone` into `~/.hermes/plugins/` | `plugins/.install-metadata.json` with `source`, `revision`, `pinned` | `hermes plugins check-updates` / `update`; a pinned plugin does not move; local edits are autostashed and reapplied |
| MCP server | `hermes mcp install <name>` from `optional-mcps/<name>/manifest.yaml` in the Hermes repo; writes the server into `config.yaml` | the manifest ships with Hermes | "MCPs are never auto-updated"; re-run install |
| Whole agent | `hermes profile install <git repo>` with a `distribution.yaml` | git tags, branches, SHAs | `hermes profile update` |
| Skill bundle | `~/.hermes/skill-bundles/<slug>.yaml` | none | none: it is an alias for a list of skill names |

Sources: [`H:website/docs/user-guide/features/plugins.md`](https://github.com/NousResearch/hermes-agent/blob/8a7a5a905fc66ed82b922587a8e3c011e819dbb5/website/docs/user-guide/features/plugins.md), [`H:website/docs/user-guide/features/plugin-catalog.md`](https://github.com/NousResearch/hermes-agent/blob/8a7a5a905fc66ed82b922587a8e3c011e819dbb5/website/docs/user-guide/features/plugin-catalog.md), [`H:hermes_cli/plugins_provenance.py`](https://github.com/NousResearch/hermes-agent/blob/8a7a5a905fc66ed82b922587a8e3c011e819dbb5/hermes_cli/plugins_provenance.py), [`H:hermes_cli/plugins_cmd_install.py`](https://github.com/NousResearch/hermes-agent/blob/8a7a5a905fc66ed82b922587a8e3c011e819dbb5/hermes_cli/plugins_cmd_install.py), [`H:website/docs/user-guide/features/mcp.md`](https://github.com/NousResearch/hermes-agent/blob/8a7a5a905fc66ed82b922587a8e3c011e819dbb5/website/docs/user-guide/features/mcp.md), [`H:website/docs/user-guide/profile-distributions.md`](https://github.com/NousResearch/hermes-agent/blob/8a7a5a905fc66ed82b922587a8e3c011e819dbb5/website/docs/user-guide/profile-distributions.md). The plugin, MCP and distribution rows rest on the docs plus a skim of the code; the exact shape of `.install-metadata.json` beyond those three keys was not read. **Partly unverified.**

The plugin catalog is the closest thing Hermes has to a reviewed, pinned library. Each entry is a YAML file in `plugin-catalog/` naming a `repo` and the exact `sha` a maintainer reviewed. Installs check out that commit, and moving the pin takes another reviewed pull request:

```yaml
name: skill-router
repo: https://github.com/xXLODXx/hermes-skill-router
sha: 445c32f9b274f53bf3d37ce1b37ec1b3e5530ac4
description: Evidence-based, session-safe skill routing with auditable decisions and opt-in learning
maintainer: xXLODXx
tier: community
```

(from [`H:plugin-catalog/skill-router.yaml`](https://github.com/NousResearch/hermes-agent/blob/8a7a5a905fc66ed82b922587a8e3c011e819dbb5/plugin-catalog/skill-router.yaml), trimmed.)

## Which standards it follows

- **Skill format: the Agent Skills spec.** The docs state compatibility with [agentskills.io](https://agentskills.io/specification). A skill is a directory with `SKILL.md` and `name` / `description` frontmatter. Hermes-specific fields sit under `metadata.hermes` (tags, category, `requires_toolsets`, `config`), which is where the spec puts extensions.
- **Discovery: skills.sh and well-known endpoints.** Hermes reads the skills.sh catalog and its `skills.sh.json` groupings file, and the `/.well-known/skills/index.json` convention.
- **Lock: none.** `lock.json` is Hermes's own format. A search of the repo's Python and docs for `skills-lock` and `skills_lock` found nothing, so Hermes neither reads nor writes the `skills-lock.json` that the `skills` CLI (and kinby's own `skills-lock.json`) uses.
- **Shared directory: `.agents/skills/`** is named in the docs as the cross-tool convention, for both `external_dirs` and project skills.

So the standard part is the skill directory and how skills are discovered. The lock, the trust tiers, and the update rules are product decisions, and Hermes made them twice (skills and plugins) with different answers.

## What this means for kinby's library

Hermes has no hub-level store. Its unit is the profile, and every profile carries its own copies and its own lock. kinby's library sits one level above that, so the Hermes design maps onto the second decision (what a pick does) more than the first.

Worth taking:

- **A pick is a copy, with a lock entry beside it.** The agent owns its copy, can edit it, and the lock says where it came from.
- **A content hash over sorted `(relative path, bytes)` as the "locally edited?" test.** One function, used on both the fetched bundle and the directory on disk. It is what makes "skip edited skills on update" a three-line check.
- **Never overwrite an edited copy without an explicit force.** Hermes applies this to bundled and hub skills alike.
- **Resolve the tree first, then fetch every file at that revision.** It avoids mixing bytes from two commits.
- **Update only from the recorded source.** Never re-resolve a bare name at update time.
- **The plugin catalog's shape for a reviewed library entry:** repo, exact 40-hex commit, optional subdirectory.
- **Refuse symlinks and binaries; validate every relative path** before writing, and validate the recorded install path before deleting.

Not worth taking:

- **Skills without a commit pin.** A Hermes skill install cannot be reproduced, and `snapshot export` carries no revision. kinby wants "at a commit", which is how Hermes plugins work, not how its skills work.
- **Two provenance files with two hash algorithms** (`lock.json` with SHA-256, `.bundled_manifest` with MD5). Shipped, git-sourced and local skills can share one record with an `origin` field.
- **No diff before an update.** The check prints a status word. Hermes can only diff bundled skills, because only those have a local stock copy to compare with. A hub-level library that keeps the upstream copy gets the diff for free.
- **Bare skill names as lock keys** across registries. The update code carries a workaround for the collisions this allows.
- **Update implying force.** In Hermes an update also overrides a `caution` block. Overwrite and scan-override should be separate switches.
- **Hard-coded trusted repos.** For a single-user product, the user adding a repository is the trust decision.
- **The regex scanner as a gate.** It is about 950 lines of patterns with documented false positives and a documented blind spot, and at version 10 it is still changing. Its verdict is useful as a signal next to a diff; as the thing that decides, it is a large maintenance cost for kinby.
- **Seven registry adapters.** One git source covers kinby's stated need.

## Not verified

- No command was run. Behavior is read from code at one commit.
- The lock example is assembled from the writer, not copied from a real file.
- How skill scripts are constrained at run time.
- `tools/skills_ast_audit.py` (92 lines) was not read; it may add checks on Python files.
- The full field list of `plugins/.install-metadata.json` and of `distribution.yaml`.
- The ClawHub, LobeHub and browse.sh adapters were not read beyond their source ids.
- The claim that the Agent Skills spec reserves `metadata` for extensions is from memory of the spec, not re-read here.
