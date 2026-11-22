# An upload is a file in the workspace, and a turn names it

Grilling [#1223](https://github.com/jorgesolerrr/kinby/issues/1223). The Finance instance works from bank exports, statements, payslips and screenshots, and the only way to give it one was to paste text. The web composer has no file input, and `thread.turn.start` takes only a message.

## Decision

- **An upload is a file in the workspace.** It lands at `uploads/<YYYY-MM-DD>/<name>`, with the name made safe for a path and ` (2)` added on a clash, so an upload never overwrites a file. Any kind of file is accepted. An upload is the user's own content, put there by the user, so the Workspace rule that kinby never writes there on its own behalf still holds.
- **The file goes up when the user picks it, and the turn names it.** `upload.put` takes a name and base64 content and returns the path. `thread.turn.start` takes the paths in `uploads`, and `TurnStarted` records only the paths. The transcript keeps the reference, never the bytes, as a delivery keeps only that it arrived (ADR 0117).
- **An image goes straight to the model.** When the model is sent the thread, each image upload becomes an image block on its turn's user message, read from the workspace at that moment and scaled down if it exceeds the provider's limit. Every upload is also named on an `Uploaded:` line after the user's text. A file that is gone is named as gone in place of its block.
- **Everything else is read with code.** The agent reads a PDF, a CSV or a spreadsheet the way it reads any workspace file, writing a script when it needs one. No PDF or document block is sent to the model.
- **Image blocks stay in later turns.** ADR 0122 clears only tool results. A follow-up about the same screenshot is the usual case, and a compaction ends the image's life in the history.
- **10 MB per file.** The instance enforces it in the contract model, and the composer checks first.

## Considered options

- **An attachments store in the instance.** Rejected. The agent's file tools never reach the instance, so it would need a second read path, and a script couldn't open the file.
- **The bytes inside `thread.turn.start` and the transcript.** Rejected. The transcript would grow with every statement, and sending would wait on the upload.
- **A `view_image` tool the agent calls to look.** Rejected for uploads. The user already chose to show the image, so a tool only adds a model call. It may come later for the agent's own browser screenshots.
- **PDFs as document blocks.** Rejected. Finance needs the numbers in a statement, which a script extracts reliably. A scanned page can be rendered to an image by code.
- **A separate HTTP upload endpoint.** Rejected for now. One frame on the existing socket carries a 10 MB file, as `BrowserFile` already does on the hub route.

## Consequences

- The client socket carries larger frames. The instance's `/ws` and the hub's relay to it accept a frame sized for a 10 MB file in base64, as the hub route does for browser files.
- An upload lands between two turns' snapshots, so it appears in no turn's workspace diff. A revert to before its turn keeps it, and a revert to an earlier turn removes it.
- In a repository workspace, an upload is an untracked file in the user's repository. Ignoring `uploads/` is the user's call.
- A file the user picks and never sends stays in the workspace. Nothing cleans it up.
- An upload never enters memory by itself. The agent remembers what it learns from one as it does from a chat, and the ingestion pipeline stays a separate feature.
- Still deferred: files the agent hands back, files a routine receives on a delivery, and a tool for the agent to view an image it made.
