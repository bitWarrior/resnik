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
