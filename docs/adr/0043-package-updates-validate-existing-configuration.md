# Package updates validate existing configuration

Before stopping an instance for a package update, validate its existing configuration and routine wrappers against the candidate image. Unsupported configuration blocks the update with a specific explanation. A difference between the template version and the installed package version alone produces a notice, because copied configuration belongs to the instance. Validation must leave those copies and the current runtime unchanged.

Decision agreed during [Package contract and the software factory as the first package](https://github.com/jorgesolerrr/kinby/issues/216).
