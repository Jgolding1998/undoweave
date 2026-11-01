# Research and design choices

Reviewed on October 31, 2026. These are useful existing projects, not a claim that
UndoWeave replaces them. No upstream implementation is copied into the package.

| Project | What it already does | What influenced UndoWeave |
| --- | --- | --- |
| [pi-rewind](https://github.com/arpagon/pi-rewind/tree/91611ad87992fb7b635a41ba68f67916ff6e6ae3) (MIT) | Pi-integrated Git checkpoints, preview, restore, recovery checkpoints and a redo stack | Separate the core from agent adapters; make preview a first-class step; treat recovery as part of the operation. Our core instead records individual writes and preserves separate later edits where it can identify them conservatively. |
| [Aider](https://github.com/Aider-AI/aider/blob/5dc9490bb35f9729ef2c95d00a19ccd30c26339c/aider/commands.py) | Its undo method checks session ownership of the last commit and refuses dirty affected files | Refusing is a successful safety outcome. Never label an unchanged, safely refused operation as data loss. UndoWeave uses explicit expected hashes and a reviewed plan rather than Git commit ownership. |
| [OrcaReplay](https://github.com/Continuum-AI-Corp/OrcaReplay/blob/a9178ca26af95cfd3231b8eb2da4ae11dbbd0a84/README.md) (Apache-2.0 code) | Recorded agent runs, replay and branching runs onto other models | Keep reproducible examples free of model calls. Its proxy/trace workflow answers different debugging questions; we did not benchmark it as a file-undo tool. |

Existing checkpoint and replay tools are substantial products. UndoWeave's narrow
experiment is whether a local MCP for explicitly recorded edits can make selective
recovery useful across clients, including a folder without Git.

## Decisions worth testing with users

- **Explicit attribution.** Agents write through the MCP tool and supply an actor
  label. This is useful operation metadata, not authenticated identity. Shell
  writes and ordinary editor changes are not automatically attributed.
- **Conservative merging.** Separate later lines can survive an undo. Overlap,
  repeated ambiguous context, or complexity limits can cause a refusal. A safe
  refusal still requires a person to resolve the change.
- **Review before mutation.** Preview produces a one-use plan tied to the current
  file. The client must obtain the user's approval before applying an undo.
- **Local scope.** No hosted account, model calls, shell tool, or network listener
  is necessary. MCP clients can still transmit tool results to their provider.
- **Small operations.** One edit changes one text file. Whole-agent transactions,
  binary recovery, automatic hooks, and a visual timeline are future experiments.

## What has actually been compared

See [COMPARISON.md](COMPARISON.md) for the measured eight-fixture comparison
against unchanged pi-rewind core and Aider's exact undo method. This is an exposed
development suite. It does not establish broad accuracy, superiority, market demand,
or behavior across every version or integration of those tools.
