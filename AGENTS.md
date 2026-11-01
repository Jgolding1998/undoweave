# Working on UndoWeave

Read SECURITY.md before changing file access, journal recovery, or merge behavior.
Run `python -m unittest discover -s tests -v` with the `mcp,dev` extras installed.
Run `python -m undoweave demo` only in its built-in disposable project.

Never test writes/undo against a real user's working tree. Never publish a local
journal, private source, preview token, credentials or files under work/.

Keep the project root pinned at startup, MCP on stdio, and all file changes behind
expected hashes. Do not add shell execution, fuzzy conflict resolution, a force
switch, or automatic deletion/repair of journals. Add independent regressions
before relaxing any refusal. Do not claim a test count proves absence of data loss.

For using the MCP as a client, see docs/AGENT_INSTRUCTIONS.md. These development
instructions do not authorize external messages, releases or changes to a user's
MCP settings.
