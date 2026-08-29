# Core tool names are reserved

Core tools implement harness contracts and must remain present on every turn. If a plugin uses a core tool's name, the tool snapshot keeps the core tool and warns about both sources. Refusing the turn would make one plugin mistake disable unrelated work, while letting the plugin win would break the harness contract; instance tools can still replace same-named package tools under ADR 0010.
