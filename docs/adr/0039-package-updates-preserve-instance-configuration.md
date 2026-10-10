# Package updates preserve instance configuration

Superseded for factories by [ADR 0084](0084-an-instance-update-syncs-the-files-its-factorys-template-supplied.md).

Package initialization copies editable prompts, permissions, and routines into the instance. Tools, packaged skills, and routine implementations remain in the installed distribution. Updates replace the installed parts and preserve the copies, because those files become the user's configuration after initialization. Record the version that supplied the copies and report differences from the installed version. Automatic rewriting and template merging are outside the first version.

Decision agreed during [Package contract and the software factory as the first package](https://github.com/jorgesolerrr/kinby/issues/216).
