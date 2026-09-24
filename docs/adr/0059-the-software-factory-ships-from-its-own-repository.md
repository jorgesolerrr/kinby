# The software factory ships from its own repository

The software factory left kinby for [kinby-code-factory](https://github.com/jorgesolerrr/kinby-code-factory), which publishes the `kinby-code-factory` distribution as package `coder` ([spec](https://github.com/jorgesolerrr/kinby-code-factory/issues/1), [migration](https://github.com/jorgesolerrr/kinby-code-factory/issues/2)). The pipeline, babysitting, their tests, the packaged skills, the coder's instance template, its image recipe, and its setup and migration guides moved with it. kinby keeps no factory code and no `instances/coder`.

kinby's base image ships git and uv only. Coding clients arrive through the factory's image recipe ([ADR 0042](0042-the-coder-package-owns-its-image-recipe.md)), so a vanilla instance carries none. The entrypoint runs its gh and Codex steps only when the image has those programs.

The hub owns the coder's lifecycle after adoption, and CI rolls kinby and factory commits to it through `instance.update` ([ADR 0057](0057-ci-updates-instances-through-an-update-only-hub-token.md)). `docker/update.sh`, its cron line, `compose.yaml`, `compose.public.yaml`, `docker/Caddyfile`, and the Compose deploy wizard are retired. This replaces the update policy of [ADR 0030](0030-the-coder-runs-on-a-public-box-behind-caddy.md). `compose.hub.yaml` is the reference deployment.
