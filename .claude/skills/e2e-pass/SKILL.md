---
name: e2e-pass
description: Deploy main to the playground hub and run end-to-end pass issues in Playwright Chromium, filing each failure as a needs-triage issue. Invoked as /e2e-pass #N [#M ...].
disable-model-invocation: true
---

Run each pass issue the user names against the playground hub at `kinby.jorgesolerrr.dev`. A pass issue is a `ready-for-human` issue whose body holds a checklist of `- [ ]` boxes. The pass is done when every box is ticked, linked to a failure issue, or listed for Jorge in the report, and `/triage` has run.

Run every command from the repository root in Git Bash. The helpers sit next to this file:

- `deploy.sh` pulls main on the box, rebuilds the hub, and runs an instance update on its instances.
- `hub.sh` runs `hub.py` inside the hub container, signed in with the hub's access token: `instances`, `update`, `secrets`, `cancel`.
- `browser.py` drives Playwright's bundled Chromium with the stored browser session.

## Steps

1. **Read the passes.** `gh issue view <N>` for each number. Collect the unticked boxes in order, and what each one needs: which instances, a routine that has fired, a stopped instance, a secret. Done when you hold that list for every pass issue.

2. **Deploy.** Run `bash .claude/skills/e2e-pass/deploy.sh` with a ten-minute timeout, or in the background, since the hub image and each instance image build on the box. With no arguments it updates every active instance that runs another revision. Pass hub instance IDs to update only those. Done when it exits 0 and every instance line ends in `succeeded` or `already on`.

   A hub that fails to build or to listen stops the pass. Report the output to Jorge. A failed instance update is a failure like any box. File it (step 5) and go on with the instances that are up.

3. **Open the browser session.** Run a first check script (step 4) that loads `/`. Exit code 3 means the stored browser session is missing or the hub rejected it. Sign in with the hub's access token from the box, piped so it is never printed:

   ```sh
   ssh playground cat kinby-hub/access-token | uv run .claude/skills/e2e-pass/browser.py login kinby.jorgesolerrr.dev --token-stdin
   ```

   If the hub refuses that token, run `login` without `--token-stdin`. It opens a Chromium window. Ask Jorge to sign in there, once. Done when a check script exits 0.

4. **Walk each checklist.** Take the boxes in order. Reuse the instances already on the hub (`bash .claude/skills/e2e-pass/hub.sh instances` lists them), and create an instance only when the box is about creating one. For each box, write a check script in your scratchpad and run it:

   ```sh
   uv run .claude/skills/e2e-pass/browser.py run kinby.jorgesolerrr.dev <script.py>
   ```

   The script starts with `page` and `context` bound, and relative URLs resolve against the hub. It drives the page with Playwright's sync API, saves screenshots to the scratchpad, and prints what it found. Read the screenshots before you judge. Add `--headed` only to watch a step. A box about the CLI runs over ssh in the instance's container, which is named after its hub instance ID: `ssh playground docker exec <id> kinby stats`.

   Judge each box against its own words. Tick it when everything it names happened. File it (step 5) when any part did not. A box you cannot judge alone, such as a design call or a wait longer than the session, stays unticked and goes in the report. Done when every box has one of those three outcomes.

5. **File each failure.** One failure, one issue. Search first with `gh issue list --state open --label needs-triage --search "<words>"`. An open issue for the same failure gets a comment instead of a twin. Otherwise:

   ```sh
   gh issue create --label needs-triage --title "<what is wrong, as a statement>" --body-file <file>
   ```

   The body says what you did, what the box expected, and what happened instead (the error text, the operation's steps, what the screenshot shows). It names the hub instance ID and the revision deployed, and ends with `Found in the e2e pass #N.` Then link the issue from its box by editing the pass issue's body:

   ```sh
   gh issue view <N> --json body --jq .body > <file>
   # "- [ ] <box text>" becomes "- [ ] <box text> (fails: #<new>)"
   gh issue edit <N> --body-file <file>
   ```

   A box that passes becomes `- [x] <box text>` the same way. Done when every failure has its issue and its box links it.

6. **Clean up.** `uv run .claude/skills/e2e-pass/browser.py cleanup` ends any Chromium process a killed run left behind, by the PIDs the helper recorded. Instances, threads, and data the pass created stay on the hub for the next pass, and the report lists them. Done when cleanup has run and no check script is still running.

7. **Hand off to triage.** Run `/triage --since #N`, with N the lowest pass issue number. Then report to Jorge, per pass issue: the boxes ticked, the failure issues filed, and the boxes left for him. The pass issues stay open for Jorge to sign off.

## Browser

All browser testing in this repository runs in Playwright's bundled Chromium through `browser.py`. Jorge's own Chrome, his profile, and the Chrome extension (`mcp__claude-in-chrome__*`) stay out of it.

`browser.py` records every process it starts in `~/.kinby-e2e/pids.json` and ends them by PID when the run ends, however it ends. To end a browser yourself, run `browser.py cleanup`, or stop a PID your own command started. Never end a process by name: `taskkill /IM chrome.exe`, `Stop-Process -Name chrome`, `pkill chrome`, `killall chrome` and the like close Jorge's Chrome too, because the bundled Chromium is also `chrome.exe`.

The browser session for each hub domain lives in `~/.kinby-e2e/<domain>.json`, outside the repository. It holds the session cookie, so it is never printed, committed, or pasted. When the hub rejects it, `run` deletes it and exits 3, and `login` stores a new one.

## Seeding secrets

No step types a secret into a page. Secret values live in `~/.kinby-e2e/secrets.env` on this machine, one `NAME=value` per line, and reach the hub on stdin:

```sh
grep -E '^(NAME|OTHER_NAME)=' ~/.kinby-e2e/secrets.env | bash .claude/skills/e2e-pass/hub.sh secrets <hub-instance-id>
```

That replaces the named instance secrets through `instance.secrets.set`, then runs a container recreation so the instance reads them. When a value is missing from the file, ask Jorge to add it there. A value only kinby and GitHub have to agree on, you generate without printing it:

```sh
python -c "import secrets; print('GITHUB_WEBHOOK_SECRET=' + secrets.token_hex(32))" >> ~/.kinby-e2e/secrets.env
```

| Secret | Name to seed | Where the value comes from |
|---|---|---|
| GitHub webhook secret | The variable the routine's `signal.secret` names. The software factory uses `GITHUB_WEBHOOK_SECRET`. | Generate it, seed it, then put the same value on the repository webhook (below). |
| GitHub token | `GH_TOKEN` | Jorge, a token with `repo` scope. |
| Claude Code token | `CLAUDE_CODE_OAUTH_TOKEN` | Jorge runs `claude setup-token` himself and adds the result. |
| Model API key | `api_key` | Jorge. The hub stores it under the provider's variable, such as `ANTHROPIC_API_KEY`. |

A routine's secret name is in its file on the box: `ssh playground grep -h secret kinby-hub/instances/<id>/routines/*/ROUTINE.md`.

The repository webhook points at `https://kinby.jorgesolerrr.dev/instances/<hub-instance-id>/signals/<routine>` and carries the same secret. Build its JSON from the file, so the value never reaches a command line. For an existing hook, send `{"secret": ...}` to `repos/<owner>/<repo>/hooks/<hook-id>/config` with `--method PATCH` instead.

```sh
python - <<'PY' | gh api --method POST repos/<owner>/<repo>/hooks --input -
import json
from pathlib import Path

lines = (Path.home() / ".kinby-e2e" / "secrets.env").read_text().splitlines()
values = dict(line.strip().split("=", 1) for line in lines if "=" in line)
print(json.dumps({
    "config": {
        "url": "https://kinby.jorgesolerrr.dev/instances/<hub-instance-id>/signals/<routine>",
        "content_type": "json",
        "secret": values["GITHUB_WEBHOOK_SECRET"],
    },
    "events": ["issues", "pull_request"],
}))
PY
```

Creating an instance in the web app asks for its secret setup fields. Leave them empty. The instance then shows pending setup, and `hub.sh secrets` fills them. If the form will not submit without one, ask Jorge to type it himself.
