"""theme.py — palettes and ttk restyling (apply needs a display)."""

from tkinter import ttk

from resnik_ide import theme


class TestPalettes:
    def test_light_and_dark_have_identical_keys(self):
        assert set(theme.LIGHT) == set(theme.DARK)
        assert theme.LIGHT["name"] == "light" and theme.DARK["name"] == "dark"
        for key in ("bg", "fg", "editor_bg", "viewer_bg", "log_bg", "hl_keyword", "gutter_err", "select_bg"):
            assert key in theme.LIGHT

    def test_palette_lookup_defaults_to_light(self):
        assert theme.palette("dark") is theme.DARK
        assert theme.palette("nope") is theme.LIGHT


class TestApply:
    def test_dark_uses_clam_and_light_restores(self, tk_root):
        style = ttk.Style(tk_root)
        original = style.theme_use()
        p = theme.apply(tk_root, "dark")
        assert p is theme.DARK and theme.is_dark() and theme.current_name() == "dark"
        assert style.theme_use() == "clam"
        assert style.lookup("TButton", "background") == theme.DARK["button_bg"]
        assert style.lookup("Muted.TLabel", "foreground") == theme.DARK["muted"]
        assert tk_root.cget("background") == theme.DARK["bg"]
        # Tk file dialog IconList uses these for the file-list canvas/text.
        assert style.lookup("Treeview", "background") == theme.DARK["field_bg"]
        assert style.lookup("Treeview", "foreground") == theme.DARK["field_fg"]
        assert style.lookup("TMenubutton", "background") == theme.DARK["button_bg"]
        p = theme.apply(tk_root, "light")
        assert p is theme.LIGHT and not theme.is_dark()
        assert style.theme_use() == original
        assert style.lookup("Muted.TLabel", "foreground") == theme.LIGHT["muted"]

    def test_style_text_and_menu(self, tk_root):
        import tkinter as tk
        t = tk.Text(tk_root)
        theme.style_text(t, theme.DARK, kind="log")
        assert t.cget("background") == theme.DARK["log_bg"]
        theme.style_text(t, theme.LIGHT, kind="field")
        assert t.cget("background") == theme.LIGHT["field_bg"]
        theme.style_text(t, theme.DARK, kind="viewer")
        assert t.cget("background") == theme.DARK["viewer_bg"]
        m = tk.Menu(tk_root, tearoff=False)
        sub = tk.Menu(m, tearoff=False)
        m.add_cascade(label="Sub", menu=sub)
        theme.style_menu(m, theme.DARK)
        assert str(m.cget("background")) == theme.DARK["bg"]
        assert theme.all_menus(m) == [m, sub]
        theme.apply(tk_root, "light")


class TestDarkFileDialogs:
    def _fake_dialog(self, tk_root):
        import tkinter as tk
        top = tk.Toplevel(tk_root, class_="TkFDialog")
        canvas = tk.Canvas(top, background="white")
        canvas.pack()
        item = canvas.create_text(10, 10, text="hello_world.hal", fill="black")
        menu = tk.Menu(top, tearoff=False)
        return top, canvas, item, menu

    def test_apply_dialog_colors_darkens_canvas_and_text(self, tk_root):
        theme.apply(tk_root, "dark")
        try:
            top, canvas, item, menu = self._fake_dialog(tk_root)
            assert theme._apply_dialog_colors(tk_root, str(top)) is True
            assert canvas.cget("background") == theme.DARK["field_bg"]
            assert canvas.itemcget(item, "fill") == theme.DARK["field_fg"]
            assert str(menu.cget("background")) == theme.DARK["bg"]
            # a text item the dialog draws later gets caught by a re-apply
            item2 = canvas.create_text(10, 30, text="new.hal", fill="black")
            theme._apply_dialog_colors(tk_root, str(top))
            assert canvas.itemcget(item2, "fill") == theme.DARK["field_fg"]
            # non-black fills (selection highlight) are left alone
            sel = canvas.create_text(10, 50, text="sel", fill="#ffffff")
            theme._apply_dialog_colors(tk_root, str(top))
            assert canvas.itemcget(sel, "fill") == "#ffffff"
            top.destroy()
        finally:
            theme.apply(tk_root, "light")

    def test_map_binding_restyles_on_open(self, tk_root):
        import time
        theme.apply(tk_root, "dark")
        try:
            theme.enable_dark_file_dialogs(tk_root)
            top, canvas, item, _menu = self._fake_dialog(tk_root)
            top.geometry("+80+80")
            top.deiconify()
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and \
                    canvas.cget("background") != theme.DARK["field_bg"]:
                tk_root.update()
                time.sleep(0.02)
            assert canvas.cget("background") == theme.DARK["field_bg"]
            assert canvas.itemcget(item, "fill") == theme.DARK["field_fg"]
            top.destroy()
        finally:
            theme.apply(tk_root, "light")

    def test_light_mode_leaves_dialogs_alone(self, tk_root):
        theme.apply(tk_root, "light")
        theme.enable_dark_file_dialogs(tk_root)
        top, canvas, item, _menu = self._fake_dialog(tk_root)
        top.deiconify()
        tk_root.update()
        assert canvas.cget("background") == "white"
        assert canvas.itemcget(item, "fill") == "black"
        top.destroy()
