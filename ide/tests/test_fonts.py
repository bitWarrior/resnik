"""fonts.py — named-font control (needs a display)."""

from tkinter import font as tkfont

from resnik_ide import fonts


class TestFonts:
    def test_defaults_and_apply_and_reset(self, tk_root):
        def_fam, def_size = fonts.default_ui_font(tk_root)
        assert def_fam and def_size >= 6
        fam, size = fonts.apply_ui_font(tk_root, "DejaVu Sans", 13)
        assert (fam, size) == ("DejaVu Sans", 13)
        assert tkfont.nametofont("TkDefaultFont", root=tk_root).cget("family") == "DejaVu Sans"
        assert int(tkfont.nametofont("TkMenuFont", root=tk_root).cget("size")) == 13
        assert int(tkfont.nametofont("TkSmallCaptionFont", root=tk_root).cget("size")) == 12
        assert tkfont.nametofont("TkHeadingFont", root=tk_root).cget("weight") == "bold"
        assert fonts.current_ui_font(tk_root) == ("DejaVu Sans", 13)
        # "" / 0 → platform default
        assert fonts.apply_ui_font(tk_root, "", 0) == (def_fam, def_size)
        assert fonts.current_ui_font(tk_root) == (def_fam, def_size)

    def test_size_clamped(self, tk_root):
        assert fonts.apply_ui_font(tk_root, "", 1)[1] == fonts.MIN_SIZE
        assert fonts.apply_ui_font(tk_root, "", 99)[1] == fonts.MAX_SIZE
        fonts.apply_ui_font(tk_root, "", 0)

    def test_family_lists(self, tk_root):
        fams = fonts.families(tk_root)
        assert fams and fams == sorted(fams, key=str.lower) and len(set(fams)) == len(fams)
        mono = fonts.monospace_families(tk_root)
        assert set(mono) <= set(fams)
        assert fonts.default_mono_family(tk_root)
