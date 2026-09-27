# A sign-in replaces credentials only when it succeeds

This amends [ADR 0065](0065-subscription-logins-are-url-and-code-only.md). The setup container used to mount the login's volume where the login keeps its credentials. `codex login --device-auth` clears `auth.json` as soon as it starts, so a Sign in again that failed or expired left a signed-in Codex signed out.

The setup container now signs in on an empty tmpfs at the login's volume path and mounts the login's volume at `/kinby/login`. The hub runs the command as `sh -c '"$@" && cp -a <volume path>/. /kinby/login/' sh <command...>`, so the files reach the volume only when the command exits 0. The copy replaces files with the same name and deletes nothing else, because the Codex volume also holds the instance's sessions and config.

A failed, expired or interrupted sign-in leaves a signed-in login signed in. Its operation still fails and says why. A login that never signed in still reads failed.

Decision agreed during [Sign in again keeps the working login](https://github.com/jorgesolerrr/kinby/issues/341), shipped with #353.
