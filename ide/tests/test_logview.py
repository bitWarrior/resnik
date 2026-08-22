"""LogView / LogSink widget tests (need a display)."""

from resnik_ide.dispatch import Dispatcher
from resnik_ide.logview import LogSink, LogView


class TestLogView:
    def test_log_appends_message_with_level_tag(self, tk_root):
        v = LogView(tk_root)
        v.log("hello", "ok")
        v.log("bad", "error", timestamp=False)
        text = v.get_text()
        assert "hello" in text and text.endswith("bad")
        # second line carries the 'error' tag, first the 'ok' tag
        assert "ok" in v.text.tag_names("1.12")
        assert "error" in v.text.tag_names("2.0")
        assert v.text.cget("state") == "disabled"

    def test_unknown_level_falls_back_to_info(self, tk_root):
        v = LogView(tk_root)
        v.log("x", "nope", timestamp=False)
        assert "info" in v.text.tag_names("1.0")

    def test_clear_and_save(self, tk_root, tmp_path):
        v = LogView(tk_root)
        v.log("one", timestamp=False)
        v.log("two", timestamp=False)
        out = tmp_path / "ide.log"
        v.save_to(str(out))
        assert out.read_text(encoding="utf-8") == "one\ntwo\n"
        v.clear()
        assert v.get_text() == ""

    def test_click_at_invokes_link_callback(self, tk_root):
        v = LogView(tk_root)
        hits = []
        v.log("plain", timestamp=False)
        v.log("jump here", "diag", on_click=lambda: hits.append(1), timestamp=False)
        assert v.click_at("1.0") is False
        assert v.click_at("2.3") is True
        assert hits == [1]
        v.clear()
        assert v.click_at("1.0") is False

    def test_mouse_click_on_link_invokes_callback(self, tk_root):
        v = LogView(tk_root)
        v.pack(fill="both", expand=True)
        hits = []
        v.log("jump here", "diag", on_click=lambda: hits.append(1), timestamp=False)
        tk_root.deiconify()
        tk_root.update()
        bbox = v.text.bbox("1.2")
        assert bbox is not None
        x, y = bbox[0] + 1, bbox[1] + 1
        # Text tag bindings dispatch against the "current" mark, which Tk
        # positions from pointer motion — so move, then press.
        v.text.event_generate("<Motion>", x=x, y=y)
        v.text.event_generate("<ButtonPress-1>", x=x, y=y)
        v.text.event_generate("<ButtonRelease-1>", x=x, y=y)
        tk_root.update()
        assert hits == [1]


class TestLogSink:
    def test_log_posts_to_view_via_dispatcher(self, tk_root):
        v = LogView(tk_root)
        d = Dispatcher(tk_root)
        sink = LogSink(v, d)
        sink.log("from worker", "warn")
        assert v.get_text() == ""  # nothing until the main thread drains
        d.drain()
        assert "from worker" in v.get_text()

    def test_file_like_write_splits_lines(self, tk_root):
        v = LogView(tk_root)
        d = Dispatcher(tk_root)
        sink = LogSink(v, d, level="raw")
        sink.write("alpha\nbe")
        sink.write("ta\n\n")
        sink.write("tail-no-newline")
        d.drain()
        lines = v.get_text().splitlines()
        assert [ln.split("] ", 1)[1] for ln in lines] == ["alpha", "beta"]
        sink.flush()
        d.drain()
        assert v.get_text().splitlines()[-1].endswith("tail-no-newline")
        assert sink.isatty() is False


class TestLogFont:
    def test_default_and_zoom_clamped(self, tk_root):
        v = LogView(tk_root)
        assert v.font_size == LogView.DEFAULT_FONT_SIZE
        assert v.zoom(+1) == 11 and v.font_size == 11
        assert int(v.bold_font.cget("size")) == 11
        assert v.zoom(0) == LogView.DEFAULT_FONT_SIZE
        assert v.set_font_size(1) == 6 and v.set_font_size(100) == 40

    def test_initial_size_and_callback(self, tk_root):
        seen = []
        v = LogView(tk_root, font_size=14, on_font_size=seen.append)
        assert v.font_size == 14 and seen == []  # constructor does not notify
        v.zoom(-1)
        assert seen == [13]
        assert v.text.tag_cget("raw", "font") == str(v.font)
        assert v.text.tag_cget("cmd", "font") == str(v.bold_font)
