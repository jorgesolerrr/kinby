# System prompt sections use a fixed order

The runner assembles one system message from named sections at each turn boundary. It orders the kinby preamble, the behavior prompt, workspace conventions, the reserved skills catalogue, the profile, and the environment. Missing file sections are skipped. The environment stays last so a date change affects only the prompt tail, which preserves the longest possible provider cache prefix.
