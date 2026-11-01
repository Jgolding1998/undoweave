# Bounded efficiency measurements

These are exposed synthetic development fixtures, not a whole-product ranking. The benchmark uses this project's actual released merge code and real current MCP calls. It makes no model or network requests.

Recorded 2026-11-01T01:24:15.021095+00:00 on Windows 11 (AMD64), Python 3.13.11, official MCP SDK 2.3.0. [Raw samples, per-call counts and exact source hashes](../benchmarks/performance-results.json) identify what was measured. No home-directory or machine hostname is stored.

## Many small inverse hunks

The baseline is unchanged `src/undoweave/merge.py` from [v0.1.0 / e37c81a](https://github.com/Jgolding1998/undoweave/blob/e37c81ad31cc2c8b26ad0c4a0849843f3ccc7626/src/undoweave/merge.py), extracted with `git show`. The current merge partitions all three snapshots using unchanged lines that appear exactly once in each snapshot and retain their order. A region changed both by the recorded edit and later work is refused.

**The v0.1.0 baseline has a known repeated-line silent-corruption bug.** It is included only as a historical timing reference on the exact-oracle fixture below, which does not trigger that bug. This comparison never recommends using the unsafe old release.

Each fixture has unique lines. Agent A changes alternating lines, leaving unchanged unique separators. A later human changes the final line, with unchanged unique text between that line and A's last edit. Both implementations receive the exact same inputs. Every accepted result must exactly equal an independently constructed expected file with A's changes removed and the human line retained. Timings cover only `inverse_merge`, excluding filesystem, SQLite, MCP and model work. Median of 3 repetitions; baseline/current execution order alternates between repetitions, without a separate warmup.

| Lines | Released median ms | Stable-anchor median ms | Released/current time ratio |
| --- | ---: | ---: | ---: |
| 500 | 43.447 | 1.380 | 31.49 |
| 1,000 | 222.443 | 3.025 | 73.53 |
| 2,000 | 716.549 | 4.884 | 146.71 |
| 4,000 | 2872.577 | 9.597 | 299.32 |

The acceptance policy is stricter: adjacent changes without an unchanged unique separator and ambiguous repeated-context edits may require manual review even when the released matcher accepted them. This timing fixture is accepted by both versions and does not compare their overall acceptance rates. The stable-anchor change addresses unsafe repeated-text alignment; timing results do not establish correctness by themselves.

This workload deliberately stresses many small inverse regions. It does not establish the same improvement for one edit, repeated text, all merge shapes or complete agent workflows. Source and comparison limits remain necessary. There is no timing-based unit-test threshold.

## Real MCP edit bodies

Both flows edit the same known location in a 1000-line, 64,000-byte ASCII file. The smaller flow requests metadata, then lines 499-501, then replaces one unique literal on line 500. Its full-file hash still binds the edit. The extra metadata/range call overhead is included. The edit location is supplied by the fixture: search and discovery costs are not measured.

| Edit flow | Tool calls | Request-body bytes | Response-body bytes | Total body bytes |
| --- | ---: | ---: | ---: | ---: |
| Full read + whole-file write | 2 | 65,231 | 132,603 | 197,834 |
| Metadata read + 3-line read + exact replacement | 3 | 401 | 2,721 | 3,122 |

The measured edit bodies were **98.42% smaller** in the second flow. These are actual tool arguments and returned `CallToolResult` models, reserialized as compact UTF-8 JSON. Response counts include both the text block and `structuredContent`. JSON-RPC framing, discovery, initialization and process startup are excluded. This is not a token-count, API-cost, transport-packet, filesystem-I/O or end-to-end speed measurement.

Both flows produced exactly the same edited bytes. Each then received the same separate human edit and completed actual `preview_undo` / `apply_undo` calls. Both restored the agent-edited line while preserving the human line. Those two validation calls are reported separately in the JSON. Including them, the measured body totals were 202,127 bytes across 4 calls and 7,417 bytes across 5 calls respectively.

The smaller responses do not imply fewer local file reads: range/metadata reads still read and hash the entire selected file, and safe replacement rechecks it. Journal snapshots still retain full before/after text. Additional tool calls can add latency. IDs and timestamps can change byte counts slightly between runs.

## Reproduce

From this Git checkout with the local `v0.1.0` tag available, install the current checkout into an isolated environment with `python -m pip install -e ".[mcp]"`, then run:

```sh
python -I benchmarks/efficiency.py
```

The script verifies the baseline tag, checks that isolated MCP child processes import the same source hashes, creates only disposable projects, and refuses to save results if measured runtime source changes during the run. It writes the JSON results and this Markdown report. It never runs undo against your working project. Repeat on a stable source checkout and compare the recorded hashes before citing numbers.
