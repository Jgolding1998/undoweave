# Release notes

## 0.4.0

- Add a read-only `setup` helper that identifies the current project, suggests
  bounded code paths and prints the pinned connection configuration. It does not
  start recording, create a journal or change client settings.
- Keep nested projects local rather than broadening to an outer monorepo;
  refuse home/drive/general-file roots and unsupported selection policies.
- Give setup connections stable project-specific names, with optional `--name`
  for setup and explicit config. Preserve literal leading-hyphen watch filenames.
- Add a companion agent skill for authorized installation/setup and evidence
  inspection; installing the Python package does not install that skill.

## 0.3.0

- Add opt-in recording for explicit project paths, including saves made outside
  MCP tools. Stable observed net changes retain source evidence and unknown
  attribution, with startup/coverage/scan limits and one recorder per journal.
- Add `inspect_change`, CLI `inspect`, pinned `config --watch`/`serve --watch`,
  foreground `watch`, and a disposable `demo --recording` restart example.
- Keep observed snapshots read-only. They are never pending write intents or
  selectively undoable agent actions; existing tool edits retain preview/undo.
- Distinguish observations and capture intervals on the actual timeline;
  refine typography and spacing using Unslop while preserving product identity.
- Expose no model reasoning or invented check results. Recorded evidence can
  support investigation after context loss; it does not establish causality.
- Publish a 20-case recording replay and a candid with/without-evidence reader
  comparison. Explicit scan-bound, per-file and journal-order metadata addresses
  ordering mistakes found in that exercise; its model effect is not yet measured.

## 0.2.1

- Show recorded changes on a continuous timeline with UTC day groups, a separate
  time column and links between an undo and the edit it reverses.
- Keep journal order visible when timestamps disagree; show unknown times and
  out-of-snapshot relationships without inventing details.
- Preserve Unicode labels in HTML exports passed through legacy PowerShell
  decoding, using equivalent numeric HTML references. The MCP resource retains
  its normal HTML output, and no new file-writing command is added.
- Add presentation and browser regressions while preserving metadata-only output,
  the no-script content policy and the existing undo preview requirements.

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
- Escape Unicode in CLI JSON so legacy Windows pipes can acknowledge successful
  edits reliably. Decoded text and file bytes remain unchanged.
- Add independent repeated-line, real Git, Windows, migration and privacy
  regressions; refresh pinned comparisons and payload measurements.

Stop every old client before [migrating history](MIGRATION.md). Never copy or
delete a marker to bypass a workspace refusal. A moved/recreated workspace may
need a fresh journal; preserve the old state privately.
