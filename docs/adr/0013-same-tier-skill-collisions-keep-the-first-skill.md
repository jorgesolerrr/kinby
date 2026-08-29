# Same-tier skill collisions keep the first skill

Skill sources have a fixed order, but two skills within the instance tier or the workspace tier can still declare the same name. The loader keeps the first skill and warns about both files, which preserves the documented order without hiding the collision or removing a working skill. Instance-over-workspace shadowing remains silent because that precedence is intentional.
