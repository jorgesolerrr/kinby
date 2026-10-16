---
name: create-instance
description: Create an instance through onboarding with instance_create, as a vanilla instance or a factory role, with its model, prompt, picks and secrets named by hub secret. Use when the user asks for a new instance or agent.
---

Onboarding is the one flow that creates an instance. The web app runs it as a form, and you run it as one `instance_create` call. The approval shows the user every value of that call, so settle them with the user first and send the call once.

## 1. Choose what it starts from

Read `factory.list`. If a factory has a role for the work, such as the software factory's `coder` for a repository, the instance takes that role in `factory_role`, as `{"factory": ..., "name": ...}`. It keeps the role for life and is written from the role's template. Anything else is a vanilla instance, built from kinby's defaults, and leaves `factory_role` out.

Done when you know the role, or know there is none.

## 2. Read the setup it asks for

Read `factory.describe` with the factory's `name` for a role: its `instances` hold each role's setup fields and logins, and its `suggested_picks` hold the skills each role proposes. Read `image.describe` for a vanilla instance.

Every instance asks for its model, its API key and its behavior prompt. The model goes in `model`, as `provider:model`, like `anthropic:claude-sonnet-5`. Every other configuration field goes in `config` by its field name, such as `behavior_prompt`. Take each field's default unless the user wants another value.

A vanilla instance needs `manifest_id`, a short name like a folder name. A role names the instance `<factory>-<role>` unless you give one. `persona_name` is the name the user calls it by.

Done when every required configuration field has a value.

## 3. Fill secrets by name only

Read `hub.secret.list`. For each secret field, such as `api_key`, put the name of a hub secret that holds the right value in `hub_secrets`, by field name: `{"api_key": "ANTHROPIC_API_KEY"}`. When no hub secret fits, leave the field out. It waits as pending setup, and the user sets it on the instance's setup card.

Never put a secret's value in any argument, even one the user pasted into the chat. The hub refuses a call that carries one. If the user pastes a secret, tell them to set it as a hub secret or on the setup card, and that it now sits in this thread.

## 4. Choose its picks

Read `library.list`. A role starts from its suggested picks, and the user may drop or add any. A vanilla instance picks what its work needs. Put the skill names in `picks`.

## 5. Create, then start

Call `instance_create`. It returns a lifecycle operation. Read `operation.get` with its `operation_id` until its state is succeeded or failed. A failed one says why in its detail and its steps.

A new instance is stopped. Tell the user what setup is pending: logins to finish and secrets to set, which they do on its setup card. Then start it with `instance_lifecycle` and `start` once the user wants it running.

If the hub refuses because the image is not prepared, the user prepares it in the web app first.
