"""theme.py — palettes and ttk restyling (apply needs a display)."""

from tkinter import ttk

from resnik_ide import theme


class TestPalettes:
    def test_light_and_dark_have_identical_keys(self):
        assert set(theme.LIGHT) == set(theme.DARK)
        assert theme.LIGHT["name"] == "light" and theme.DARK["name"] == "dark"
        for key in ("bg", "fg", "editor_bg", "log_bg", "hl_keyword", "gutter_err", "select_bg"):
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
        m = tk.Menu(tk_root, tearoff=False)
        sub = tk.Menu(m, tearoff=False)
        m.add_cascade(label="Sub", menu=sub)
        theme.style_menu(m, theme.DARK)
        assert str(m.cget("background")) == theme.DARK["bg"]
        assert theme.all_menus(m) == [m, sub]
        theme.apply(tk_root, "light")
