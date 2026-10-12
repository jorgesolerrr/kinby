---
name: adversarial-review
description: "Two-axis code review (Standards, Spec) of a diff, run by the other model's CLI so the reviewer never shares the implementer's context. Use when the user wants a branch, PR, or work in progress reviewed, asks to \"review since X\", or another skill needs a review loop."
---

Two-axis review of the diff between `HEAD` and a fixed point:

- **Standards**: does the code conform to this repo's documented coding standards?
- **Spec**: does the code faithfully implement the originating issue / spec?

Both axes run as **parallel reviewer processes** in the other model's CLI, so they share neither each other's context nor yours. The session that wrote the code believes the code is right; a fresh model with no memory of writing it does not. This skill dispatches the reviewers and aggregates what they return.

A calling loop may pass two more inputs:

- **Check result**: the outcome of the repo's typecheck and test suite on `HEAD`. Reviewers read it; they never run the suite themselves.
- **Round history**: the previous round's head sha and a file listing that round's hard findings and how each was handled. It turns this run into a **delta round** (see step 5).

## Process

### 1. Pin the fixed point

Whatever the user (or the calling skill) said is the fixed point (a commit SHA, branch name, tag, `main`, `HEAD~5`, etc.). If none was given, use the merge base: `git merge-base HEAD <default-branch>`, where the default branch comes from `git symbolic-ref refs/remotes/origin/HEAD` (fall back to `main`, then `master`). Say which fixed point you chose in the report.

Capture the diff command once: `git diff <fixed-point>...HEAD` (three-dot, so the comparison is against the merge-base). Also note the list of commits via `git log <fixed-point>..HEAD --oneline`.

Before going further, confirm the fixed point resolves (`git rev-parse <fixed-point>`) and the diff is non-empty. On a bad ref or an empty diff, report which one and end with `Incomplete`, before any reviewer starts.

### 2. Identify the spec source

Look for the originating spec, in this order:

1. The ticket passed by the user or the calling skill. Fetch tickets per `docs/agents/issue-tracker.md`; without that file, on a GitHub remote, with `gh issue view <id> --comments`; with neither, go to 3.
2. Issue references in the commit messages (`#123`, `Closes #45`, GitLab `!67`, etc.), fetched the same way.
3. A path the user passed as an argument.
4. A spec file under `docs/`, `docs/blueprints/`, `specs/`, or `.scratch/` matching the branch name or feature.
5. If nothing is found, ask the user where the spec is. If they say there isn't one, the **Spec** reviewer will skip and report "no spec available".

Write the fetched spec to a scratch file so the reviewer can read it without tracker access.

### 3. Identify the standards sources

Anything in the repo that documents how code should be written, such as `CODING_STANDARDS.md` or `CONTRIBUTING.md`.

On top of whatever the repo documents, the Standards axis always carries the **smell baseline** in [`references/smells.md`](references/smells.md): a fixed set of Fowler code smells (_Refactoring_, ch.3) that applies even when a repo documents nothing. The reviewer reads that file by absolute path.

### 4. Pick the reviewer CLI

Detect which agent you are and dispatch the other one. The commands carry no model flag: each CLI runs the model its own config sets.

| You are | Reviewer command (prompt on stdin, answer to a file) |
|---|---|
| Claude Code | `codex exec -s read-only -C <repo> -o <out.md> - < <prompt.md>` |
| Codex | `claude -p --allowedTools "Read,Grep,Glob,Bash(git diff:*),Bash(git log:*),Bash(git show:*)" < <prompt.md> > <out.md>` |

**Preflight.** Send the same command the prompt `Reply with the single word ok` with a 60-second timeout. An answer containing `ok` means the CLI is installed, signed in, and has credits. Anything else (missing binary, auth or credit error, timeout) means fall back: run the two reviewers as sub-agents of your own model, and name the fallback and the preflight's error under **Reviewer**, since a same-model review is the weaker result.

The reviewer is read-only: it reports, it does not edit.

### 5. Dispatch both reviewers in parallel

Write each prompt to a scratch file, start both processes in the background, and wait for both, up to 15 minutes each. Each prompt states the repo path, the exact diff command and commit list, and ends with the brief below. Reviewers read files themselves; the prompt carries paths, not pasted contents.

Every prompt also carries the **reading rule**: "Review by reading the diff, the files, and git history. The checks have already run; their result is <check result, or 'not provided'>. Your answer is the report itself."

In a **delta round** (round history given), each prompt also carries: "This is a follow-up round. The previous round reviewed up to <prev-sha>; its findings and how each was handled are in <history file>. Review `git diff <prev-sha>..HEAD`, reading the full diff only for context. Report (a) previous findings that are still unresolved, and (b) new findings in the delta. A new Standards finding on code unchanged since <prev-sha> is tagged `[suggestion]`; Spec findings keep `[hard]`."

**Standards prompt** should include:

- The full diff command and commit list.
- The list of standards-source files you found in step 3, plus the absolute path of `references/smells.md`.
- The brief: "Report, per file/hunk where relevant, (a) every place the diff violates a documented standard: cite the standard (file + the rule); and (b) any baseline smell you spot: name it and quote the hunk. Tag every bullet `[hard]` or `[suggestion]`: `[hard]` is a breach of a documented standard, cited; `[suggestion]` is everything else, and baseline smells are always `[suggestion]`. A documented repo standard overrides the baseline. Skip anything tooling enforces. One finding per bullet, each starting with the tag then `path:line`. Under 400 words. If you find nothing, answer exactly `No findings`."

**Spec prompt** should include:

- The diff command and commit list.
- The path of the scratch file holding the spec.
- The brief: "Report: (a) requirements the spec asked for that are missing or partial; (b) behaviour in the diff that wasn't asked for (scope creep); (c) requirements that look implemented but where the implementation looks wrong. Quote the spec line for each finding. Every spec finding is `[hard]`: a missing, extra, or wrong requirement is a defect, so tag each bullet `[hard]` and start it with the tag then `path:line`. Under 400 words. If you find nothing, answer exactly `No findings`."

If the spec is missing, skip the Spec reviewer. A skipped Spec axis counts as complete with no findings; name the skip in the **Reviewer** line.

### 6. Aggregate

Present the two reports under `## Standards` and `## Spec` headings, verbatim or lightly cleaned. Do **not** merge or rerank findings, because the two axes are deliberately separate (see _Why two axes_).

An axis is **incomplete** when its reviewer timed out, exited with an error, or answered with neither tagged findings nor `No findings`. Report it under its heading with whatever output exists. Do not dispatch it again: a retry is the caller's or the user's decision.

End with one line: **Reviewer** (which CLI ran, or the fallback and why), the fixed point, hard and suggestion counts per axis, and the worst hard issue _within each axis_ (if any). Don't pick a single winner across axes: that's the reranking the separation exists to prevent. A run is **clean** when neither axis returned a `[hard]` finding and neither is incomplete; suggestions alone do not make it unclean, they are the user's call. The last word is `Clean`, `Not clean`, or `Incomplete` (any axis incomplete), so a calling loop can stop on it.

## Why two axes

A change can pass one axis and fail the other:

- Code that follows every standard but implements the wrong thing → **Standards pass, Spec fail.**
- Code that does exactly what the issue asked but breaks the project's conventions → **Spec pass, Standards fail.**

Reporting them separately stops one axis from masking the other.
