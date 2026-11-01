# Contributing

Use a disposable test project. Never run test edits against somebody's working tree or upload journals and private source in issues.

Install Python 3.11+, create a virtual environment, then run:

```sh
python -m pip install -c constraints.txt ".[mcp,dev]"
python -m unittest discover -s tests -v
python -m undoweave demo
```

Reproductions must assert exact expected bytes, especially when refusing a change. Add independent edge cases before relaxing a conservative merge refusal. Keep MCP on stdio and preserve the absence of shell execution and model calls. Do not add force, automatic journal deletion, or automatic conflict resolution to bypass a safety check.

For benchmark changes, pin the upstream revision and distinguish executing an upstream method from testing its entire application. Whole-checkpoint restoration and selective undo have different intended behavior; a safe refusal is not a data-loss failure. Document cases where another tool fits better.
