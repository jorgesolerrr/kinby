# Package initialization uses declared inputs

Packages supply fixed instance templates and declare their configuration and secret inputs. Kinby combines built-in defaults, package defaults, and explicit user choices in that order, then validates the result before creating the instance. Package setup scripts are outside the first version, so initialization has one path that kinby can validate for both local and hub clients.

Templates cannot supply existing memory, transcripts, credentials, or instance identity. Secrets have no template defaults. Subscription login requirements are separate from environment fields. A new instance can remain stopped with login pending, but its factory routines cannot start until required setup is complete.

Decision agreed during [Package contract and the software factory as the first package](https://github.com/jorgesolerrr/kinby/issues/216).
