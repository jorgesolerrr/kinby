# Email and URL are setup field types

A setup field's type said only whether its value was text, so the hub took any text for a repository or a commit email. A bad repository showed up after creation, as a coder that crash-looped on `git clone`. `SetupFieldType` now has `email` and `url`, and `instance.create` refuses a malformed value on its field before anything is created. The package check refuses a malformed default the same way.

An `email` value is one address, `local@domain`, with a dot in the domain and no spaces. A `url` value is an absolute URL with a scheme and a host, as `urllib.parse` reads it, so `owner/name` and the scp form `git@github.com:owner/name.git` are refused.

## Considered options

- **A `format` attribute on text fields.** Rejected. Each type keeps one control and one check, and a format would give text fields a second axis that every client has to read. The web app renders `email` and `url` as their own inputs and still shows the hub's error, not the browser's.

Decision agreed during [Email and URL setup fields](https://github.com/jorgesolerrr/kinby/issues/332), shipped with #353.
