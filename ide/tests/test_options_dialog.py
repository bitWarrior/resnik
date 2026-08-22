"""OptionsDialog widget tests (need a display)."""

from resnik_ide.options import IdeCompileSettings
from resnik_ide.options_dialog import OptionsDialog

FULL = IdeCompileSettings(
    target="esp32-c3", emit_exe=True, opt_level="s", emit_cargo="fw", build=True,
    rust="x.rs", tokens="t.json", ast="a.json", link_search=["lib"], link_lib=["m"],
    link_obj=["i2c.o"], gen_tpl="TPL", templib=["T1", "T2"], extra_args="--build",
)


class TestOptionsDialog:
    def test_collect_reflects_initial_settings(self, tk_root):
        dlg = OptionsDialog(tk_root, FULL, source="prog.hal")
        assert dlg.collect() == FULL
        assert dlg.v_preview.get() == FULL.command_line("prog.hal")
        dlg.destroy()

    def test_preview_updates_live(self, tk_root):
        dlg = OptionsDialog(tk_root, IdeCompileSettings(), source="prog.hal")
        assert dlg.v_preview.get() == "resnik compile prog.hal"
        dlg.v_emit_exe.set(True)
        dlg.v_target.set("cortex-m")
        assert dlg.v_preview.get() == "resnik compile prog.hal --emit-exe --target cortex-m"
        dlg.lf_templib.set(["A", "B"])
        assert dlg.v_preview.get().endswith("--templib A --templib B")
        dlg.destroy()

    def test_ok_returns_edited_settings(self, tk_root):
        dlg = OptionsDialog(tk_root, IdeCompileSettings())
        dlg.v_opt_level.set("3")
        dlg.v_rust.set("  out.rs  ")
        dlg.lf_link_lib.text.insert("end", "m\n\n  c  \n")
        dlg._on_ok()
        assert dlg.result == IdeCompileSettings(opt_level="3", rust="out.rs", link_lib=["m", "c"])

    def test_cancel_returns_none(self, tk_root):
        dlg = OptionsDialog(tk_root, FULL)
        dlg.v_target.set("host")
        dlg._on_cancel()
        assert dlg.result is None

    def test_reset_to_defaults(self, tk_root):
        dlg = OptionsDialog(tk_root, FULL)
        dlg.reset_defaults()
        assert dlg.collect() == IdeCompileSettings()
        assert dlg.lf_templib.get() == []
        dlg.destroy()

    def test_list_field_append_and_clear(self, tk_root):
        dlg = OptionsDialog(tk_root, IdeCompileSettings())
        dlg.lf_link_search.append("/a")
        dlg.lf_link_search.append("/b")
        assert dlg.lf_link_search.get() == ["/a", "/b"]
        dlg.lf_link_search.clear()
        assert dlg.lf_link_search.get() == []
        dlg.destroy()
