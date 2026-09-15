# Babysit rounds count before client execution

The babysitter posts its round marker before starting the coding client. If GitHub rejects that write, the round stops without using the client or pushing changes.

Started rounds count toward the limit even when checks, pushes, or later replies fail. This bounds repeated work when GitHub accepts a push but rejects replies. Result summaries use a separate prefix so they do not count twice. GitHub remains the progress store.
