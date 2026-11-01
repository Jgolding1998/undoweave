# Release notes

## 0.2.0

Upgrade from 0.1.0 before using undo on important work. That release could accept
the wrong inverse when repeated lines aligned crosswise. Version 0.2 partitions
changes only at globally unique shared lines, and refuses regions modified by
both edits. This fixes the reproduced cases while deliberately reducing the
number of automatically accepted undos. It is not a guarantee against every
possible data-loss case.

- Bind each journal to the directory instance and a protected, source-free
  workspace marker. Nonempty v0.1 history requires reviewed local adoption.
- Verify failed replacements against observed bytes; retain unknown intents
  while allowing status/history diagnostics for unreadable pending targets.
- Collect expired/used previews, store result hashes and reserve SQLite update
  headroom before project mutation.
- Add full-hash range/metadata reads and exact unique text replacements.
- Align physical-line counting and make CRLF/CR/EOF review details explicit.
- Add a standalone read-only HTML timeline, CLI export and MCP resource.
- Add independent repeated-line, real Git, Windows, migration and privacy
  regressions; refresh pinned comparisons and payload measurements.

Stop every old client before [migrating history](MIGRATION.md). Never copy or
delete a marker to bypass a workspace refusal. A moved/recreated workspace may
need a fresh journal; preserve the old state privately.
