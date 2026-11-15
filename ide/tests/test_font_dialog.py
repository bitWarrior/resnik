# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Steven Doran
"""FontDialog widget tests (need a display)."""

from resnik_ide import fonts
from resnik_ide.font_dialog import FontDialog


class TestFontDialog:
    def test_collect_apply_reset(self, tk_root):
        applied = []
        dlg = FontDialog(tk_root, ui_family="DejaVu Sans", ui_size=12, mono_family="DejaVu Sans Mono",
                         on_apply=lambda *a: applied.append(a))
        assert dlg.collect() == ("DejaVu Sans", 12, "DejaVu Sans Mono", 11, 10)
        dlg.apply()
        assert applied == [("DejaVu Sans", 12, "DejaVu Sans Mono", 11, 10)]
        dlg.apply()  # unchanged → no second callback
        assert len(applied) == 1
        dlg.v_ui_size.set(15)
        dlg.v_editor_size.set(13)
        dlg.v_log_size.set(9)
        dlg._on_ok()
        assert applied[-1] == ("DejaVu Sans", 15, "DejaVu Sans Mono", 13, 9)
        assert dlg.result == ("DejaVu Sans", 15, "DejaVu Sans Mono", 13, 9)

    def test_defaults_when_empty_and_reset(self, tk_root):
        dlg = FontDialog(tk_root, ui_family="", ui_size=0, mono_family="", on_apply=lambda *a: None)
        def_fam, def_size = fonts.default_ui_font(tk_root)
        assert dlg.collect() == (def_fam, def_size, fonts.default_mono_family(tk_root), 11, 10)
        dlg.v_ui_family.set("Serif")
        dlg.v_editor_size.set(20)
        dlg.reset_defaults()
        assert dlg.v_ui_family.get() == def_fam
        assert dlg.collect()[3:] == (11, 10)
        dlg._on_cancel()
        assert dlg.result is None

    def test_mono_list_toggle(self, tk_root):
        dlg = FontDialog(tk_root, ui_family="", ui_size=0, mono_family="", editor_size=12,
                         log_size=8, on_apply=lambda *a: None)
        assert dlg.collect()[3:] == (12, 8)
        mono = list(dlg.cb_mono["values"])
        dlg.v_all_mono.set(True)
        dlg._refill_mono()
        assert len(dlg.cb_mono["values"]) >= len(mono)
        dlg.destroy()

    def test_ok_after_apply_without_changes_is_silent(self, tk_root):
        applied = []
        dlg = FontDialog(tk_root, ui_family="", ui_size=0, mono_family="",
                         on_apply=lambda *a: applied.append(a))
        dlg.v_ui_size.set(14)
        dlg.apply()
        dlg._on_ok()
        assert len(applied) == 1 and dlg.result == applied[0]
