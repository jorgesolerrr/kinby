---
name: create-instance
description: Create an instance, a teammate, through onboarding with hub_write's instance_create operation, with its model, prompt, picks and secrets named by hub secret. Use when the user asks for a new instance or agent.
---

Onboarding is the one flow that creates an instance, a teammate the user talks to. The web app runs it as a form, and you run it as one `hub_write` call with the `instance_create` operation. The approval shows the user every value of that call, so settle them with the user first and send the call once. Each read below is a `kinby hub read`, and `kinby hub help instance_create` prints the arguments the call takes.

When a read this flow depends on fails, stop and tell the user what failed. Never raise an approval for a call that read was meant to check.

A factory runs on a workbench, not in an instance. When the user wants work carried by a factory, set up a line with the `line_setup` operation instead of creating an instance.

## 1. Read the setup it asks for

Read `image.describe`: it holds the setup fields and logins every instance asks for.

If the hub refuses `image.describe` because the image is not prepared, call `hub_write` with `image_prepare` and no arguments. It returns a lifecycle operation: run `kinby hub wait` with its `operation_id`, then read `image.describe` again. If the preparation fails, tell the user why, from its detail and its steps, and stop. Never ask for `instance_create` on an image that is not prepared.

Every instance asks for its model, its API key and its behavior prompt. The model goes in `model`, as `provider:model`, like `anthropic:claude-sonnet-5`. Every other configuration field goes in `setup` by its field name, such as `behavior_prompt`. Take each field's default unless the user wants another value.

Every instance needs `manifest_id`, a short name like a folder name. `persona_name` is the name the user calls it by.

Done when every required configuration field has a value.

## 2. Fill secrets by name only

Read `hub.secret.list`. For each secret field, such as `api_key`, put the name of a hub secret that holds the right value in `hub_secrets`, by field name: `{"api_key": "ANTHROPIC_API_KEY"}`. When no hub secret fits, leave the field out. It waits as pending setup, and the user sets it on the instance's setup card.

Never put a secret's value in any argument, even one the user pasted into the chat. The hub refuses a call that carries one. If the user pastes a secret, tell them to set it as a hub secret or on the setup card, and that it now sits in this thread.

## 3. Choose its picks

Read `library.list`. The instance picks what its work needs. Put the skill names in `picks`.

## 4. Create, then start

Call `hub_write` with `instance_create` and these values as its arguments. It returns a lifecycle operation. Run `kinby hub wait` with its `operation_id` until it ends. A failed one says why in its detail and its steps.

A new instance is stopped. Tell the user what setup is pending: logins to finish and secrets to set, which they do on its setup card. Then start it with the `instance_lifecycle` operation and `start` once the user wants it running.

If the hub refuses `instance_create` because the image is not prepared, prepare it and read `image.describe` again, as in step 1. Settle any value that changed with the user before you ask for `instance_create` again.
