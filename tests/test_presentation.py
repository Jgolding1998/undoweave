"""HTML output keeps journal summaries readable without expanding source data."""

from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path
import sys
import subprocess
import tempfile
import unittest

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from undoweave.presentation import render_timeline


def row(**overrides):
    value = {"id": "a" * 32, "path": "src/example.py", "actor": "demo-agent",
             "label": "Clarify the greeting", "status": "applied", "created": 1793444400.0,
             "undoes": None, "undone_by": None}
    value.update(overrides)
    return value


class Document(HTMLParser):
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.elements = []
        self.text = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))

    def handle_data(self, data):
        self.text.append(data)


class PresentationTests(unittest.TestCase):
    def test_cli_renders_real_recorded_history_without_source_or_file_changes(self):
        from undoweave.engine import Engine
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "project"
            root.mkdir()
            state = Path(temporary) / "state"
            engine = Engine(root, state)
            engine.edit("hello.txt", "PRIVATE_SOURCE_MARKER\n", "demo-agent", "missing", "A real recorded edit")
            files = {path.name: path.read_bytes() for path in root.iterdir()}
            result = subprocess.run([sys.executable, "-I", "-m", "undoweave", "--root", str(root), "--state-dir", str(state), "history", "--format", "html"],
                                    capture_output=True, encoding="utf-8", timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("A real recorded edit", result.stdout)
            self.assertIn("hello.txt", result.stdout)
            self.assertNotIn("PRIVATE_SOURCE_MARKER", result.stdout)
            self.assertNotIn(str(root), result.stdout)
            self.assertEqual({path.name: path.read_bytes() for path in root.iterdir()}, files)

    def test_renderer_keeps_only_summary_metadata_even_for_full_private_record(self):
        private = {key: "DO_NOT_EXPORT_" + key for key in (
            "before_text", "after_text", "content", "diff", "token", "preview_token",
            "before_hash", "after_hash", "current_hash", "result_hash", "arbitrary")}
        html = render_timeline([row(**private)], {
            "root": "DO_NOT_EXPORT_ROOT", "state_dir": "DO_NOT_EXPORT_STATE",
            "pending_paths": ["DO_NOT_EXPORT_PENDING_PATH"],
            "recovery_notice": "DO_NOT_EXPORT_NOTICE", "recorded_count": 1, "pending_count": 0})
        self.assertNotIn("DO_NOT_EXPORT", html)
        self.assertIn("src/example.py", html)
        self.assertIn("demo-agent", html)
        self.assertIn("self-reported", html)
        self.assertIn("Review this report before sharing", html)

    def test_untrusted_metadata_and_title_cannot_add_html_elements_or_attributes(self):
        payload = '\"><img src="https://example.invalid" onerror="alert(1)"><script>run()</script>&'
        html = render_timeline([row(id=payload, path=payload, actor=payload, label=payload,
                                    status=payload, undoes=payload, undone_by=payload)], title=payload)
        doc = Document(html)
        self.assertNotIn("img", [tag for tag, _ in doc.elements])
        self.assertNotIn("script", [tag for tag, _ in doc.elements])
        self.assertTrue(all(not key.startswith("on") for _, attrs in doc.elements for key in attrs))
        self.assertIn("&lt;img", html)
        self.assertIn(payload, "".join(doc.text))

    def test_no_network_assets_or_mutating_controls_are_present(self):
        doc = Document(render_timeline([row()]))
        for tag, attrs in doc.elements:
            self.assertNotIn(tag, {"script", "form", "button", "iframe", "object", "embed", "link"})
            self.assertNotIn("src", attrs)
            if "href" in attrs:
                self.assertTrue(attrs["href"].startswith("#"))
        policies = [attrs["content"] for tag, attrs in doc.elements
                    if tag == "meta" and attrs.get("http-equiv") == "Content-Security-Policy"]
        self.assertIn("default-src 'none'", policies[0])

    def test_pending_and_undo_relations_are_described_without_promising_undo(self):
        html = render_timeline([row(undone_by="b" * 32), row(id="b" * 32, undoes="a" * 32)],
                               {"recorded_count": 2, "pending_count": 1})
        self.assertIn("Pending operations need review", html)
        self.assertIn("Undo recorded", html)
        self.assertIn("Undone by", html)
        self.assertIn("Undo of", html)
        self.assertIn("fresh preview", html)
        self.assertNotIn("Ready to undo", html)

    def test_aborted_rows_are_not_counted_as_applied(self):
        html = render_timeline([row(status="aborted")], {"recorded_count": 0, "pending_count": 0})
        self.assertIn("Applied in journal</dt><dd>0", html)
        self.assertIn("Shown in this snapshot</dt><dd>1", html)
        self.assertIn("Not applied", html)
        self.assertIn("Newest first", html)

    def test_history_order_and_utc_timestamps_are_preserved(self):
        html = render_timeline([row(label="Newest change", created=1700000001),
                                row(label="Earlier change", created=1700000000)])
        self.assertLess(html.index("Newest change"), html.index("Earlier change"))
        self.assertIn('datetime="2023-11-14T22:13:21Z"', html)
        self.assertIn("UTC</time>", html)
        self.assertIn("Journal totals unavailable", html)

    def test_empty_partial_and_invalid_optional_metadata_have_explicit_placeholders(self):
        self.assertIn("No changes in this snapshot", render_timeline([]))
        html = render_timeline([{}], {"recorded_count": True, "pending_count": -2})
        for value in ("Recorded change", "Path unavailable", "Unlabeled actor", "Time unavailable", "Status unknown"):
            self.assertIn(value, html)
        for created in (float("nan"), float("inf"), 1e100, True, "<script>bad</script>"):
            self.assertIn("Time unavailable", render_timeline([row(created=created)]))

    def test_control_and_direction_characters_are_visible_and_nested_values_not_stringified(self):
        html = render_timeline([row(label="Rename\u202efile\n", path={"content": "HIDDEN_NESTED"})])
        self.assertIn("Rename\\u202efile\\u000a", html)
        self.assertNotIn("\u202e", html)
        self.assertNotIn("HIDDEN_NESTED", html)

    def test_history_shape_and_bound_are_validated(self):
        for history in ("private source", {}, ["private source"], [row()] * 201):
            with self.assertRaises(ValueError):
                render_timeline(history)
        with self.assertRaises(ValueError):
            render_timeline([], status="private source")


if __name__ == "__main__":
    unittest.main()
