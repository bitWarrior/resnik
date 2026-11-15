# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Steven Doran
"""Editor widget tests (need a display)."""

from resnik_ide.editor import TAB_SPACES, Editor


class TestEditor:
    def test_set_and_get_text_resets_dirty(self, tk_root):
        dirty = []
        e = Editor(tk_root, on_dirty=dirty.append)
        e.set_text("A: PROGRAM;\nCLOSE A;\n")
        assert e.get_text() == "A: PROGRAM;\nCLOSE A;\n"
        assert e.dirty is False

    def test_typing_sets_dirty_once_and_clears_error_marks(self, tk_root):
        dirty = []
        e = Editor(tk_root, on_dirty=dirty.append)
        e.set_text("line1\nline2\nline3\n")
        e.mark_error_lines([2, 3])
        assert e.error_lines == [2, 3]
        e.text.insert("1.0", "X")
        tk_root.update()
        assert e.dirty is True
        assert dirty == [True]
        assert e.error_lines == []
        e.text.insert("1.0", "Y")
        tk_root.update()
        assert dirty == [True]  # no duplicate callback while already dirty

    def test_load_save_roundtrip_adds_trailing_newline(self, tk_root, tmp_path):
        src = tmp_path / "a.hal"
        src.write_text("A: PROGRAM;\nCLOSE A;", encoding="utf-8")
        e = Editor(tk_root)
        e.load(str(src))
        assert e.get_text() == "A: PROGRAM;\nCLOSE A;"
        e.text.insert("end", "\n/* c */")
        tk_root.update()
        assert e.dirty
        out = tmp_path / "b.hal"
        e.save(str(out))
        assert e.dirty is False
        assert out.read_text(encoding="utf-8") == "A: PROGRAM;\nCLOSE A;\n/* c */\n"

    def test_goto_moves_caret_and_reports_position(self, tk_root):
        pos = []
        e = Editor(tk_root, on_cursor=lambda l, c: pos.append((l, c)))
        e.set_text("one\ntwo\nthree\n")
        e.goto(3, 4)
        assert e.cursor() == (3, 4)
        assert pos[-1] == (3, 4)
        e.goto(2)  # column 0 → start of line
        assert e.cursor() == (2, 1)
        e.goto(0)  # clamped
        assert e.cursor()[0] == 1

    def test_mark_error_lines_ignores_nonpositive(self, tk_root):
        e = Editor(tk_root)
        e.set_text("a\nb\nc\n")
        e.mark_error_lines([0, -1, 2])
        assert e.error_lines == [2]
        assert "error_line" in e.text.tag_names("2.0")
        e.clear_error_marks()
        assert e.error_lines == []
        assert "error_line" not in e.text.tag_names("2.0")

    def test_tab_inserts_spaces(self, tk_root):
        e = Editor(tk_root)
        e.pack()
        tk_root.deiconify()
        tk_root.update()
        e.set_text("")
        e.text.focus_force()
        e.text.event_generate("<Tab>")
        tk_root.update()
        assert e.get_text() == " " * TAB_SPACES

    def test_gutter_draws_line_numbers(self, tk_root):
        e = Editor(tk_root)
        e.pack(fill="both", expand=True)
        tk_root.deiconify()
        tk_root.geometry("400x300")
        tk_root.update()
        e.set_text("\n".join(f"line {i}" for i in range(1, 6)) + "\n")
        tk_root.update()
        numbers = [e.gutter.itemcget(i, "text") for i in e.gutter.find_all()]
        assert numbers[:5] == ["1", "2", "3", "4", "5"]


class TestProxyRobustness:
    """Tk's own Text class bindings call the widget command from Tcl; an
    exception escaping the gutter proxy there kills the whole mainloop."""

    def test_drag_without_press_does_not_kill_mainloop(self, tk_root, monkeypatch):
        # <B1-Motion> without a prior ButtonPress → tk::TextSelectTo uses the
        # per-widget tk::anchorN mark, which plain Tk only creates on press →
        # 'bad text index "tk::anchor1"'.  Happens in real life when dragging
        # a sash / window edge over the editor.  Must neither raise nor report.
        reported = []
        monkeypatch.setattr(tk_root, "report_callback_exception",
                            lambda *a: reported.append(a))
        e = Editor(tk_root)
        e.pack(fill="both", expand=True)
        tk_root.deiconify()
        tk_root.update()
        e.set_text("one\ntwo\n")
        e.text.event_generate("<B1-Motion>", x=30, y=10)
        tk_root.after(50, tk_root.quit)
        tk_root.mainloop()  # must return normally, not raise TclError
        assert e.get_text() == "one\ntwo\n"
        assert reported == []

    def test_unexpected_tcl_error_is_reported_not_raised(self, tk_root, monkeypatch):
        reported = []
        monkeypatch.setattr(tk_root, "report_callback_exception",
                            lambda *a: reported.append(a))
        e = Editor(tk_root)
        result = e.text.index("no such index")
        assert result == ""
        assert len(reported) == 1
        assert "no such index" in str(reported[0][1])

    def test_benign_errors_are_silent(self, tk_root, monkeypatch):
        reported = []
        monkeypatch.setattr(tk_root, "report_callback_exception",
                            lambda *a: reported.append(a))
        e = Editor(tk_root)
        e.text.edit_undo()          # nothing to undo
        e.text.index("tk::anchor1")  # missing selection anchor
        assert reported == []


class TestHighlightingAndFont:
    def test_set_text_highlights_immediately(self, tk_root):
        e = Editor(tk_root)
        e.set_text("A: PROGRAM;\n  DECLARE X INTEGER;\nCLOSE A;\n")
        assert ("2.2", "2.9") in e.highlighted("hl_keyword")
        assert ("2.12", "2.19") in e.highlighted("hl_type")
        assert e.highlighted("hl_label") == [("1.0", "1.1")]

    def test_typing_rehighlights_after_debounce(self, tk_root):
        e = Editor(tk_root)
        e.set_text("A: PROGRAM;\n")
        e.text.insert("end", "  WRITE(6) 'hi';\n")
        tk_root.update()
        # schedule fired via <<Modified>>; run the pending after() callbacks
        import time
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and not e.highlighted("hl_string"):
            tk_root.update()
            time.sleep(0.02)
        assert e.highlighted("hl_string") == [("2.11", "2.15")]

    def test_highlighting_can_be_disabled(self, tk_root):
        e = Editor(tk_root)
        e.set_text("A: PROGRAM;\nCLOSE A;\n")
        assert e.highlighted("hl_keyword")
        e.set_highlighting(False)
        assert e.highlighted("hl_keyword") == []
        e.text.insert("end", "DECLARE X INTEGER;\n")
        e.highlight_now()
        assert e.highlighted("hl_keyword") == []
        e.set_highlighting(True)
        assert e.highlighted("hl_keyword")

    def test_font_size_clamped(self, tk_root):
        e = Editor(tk_root)
        assert e.font_size == 11
        assert e.set_font_size(14) == 14 and e.font_size == 14
        assert e.set_font_size(2) == 6
        assert e.set_font_size(99) == 40
        assert int(e._bold_font.cget("size")) == 40
