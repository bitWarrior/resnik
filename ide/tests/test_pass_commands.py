"""pass_commands.parse — pure parser for the PASS keyboard vocabulary."""

from resnik_ide import pass_commands as pc


class TestParse:
    def test_spec_item_ops(self):
        assert pc.parse("SPEC 1 PRO").action == "toolchain"
        assert pc.parse("spec 2").echo == "SPEC 2 PRO"
        assert pc.parse("ITEM 1 EXEC").action == "semantic_check"
        assert pc.parse("item 3").action == "build_and_run"
        assert pc.parse("ITEM 6 EXEC").action == "met"
        assert pc.parse("OPS 9 PRO").action == "about"
        assert pc.parse("ops 0").action == "help"
        assert pc.parse("?").kind == "help" and pc.parse("help").kind == "help"

    def test_illegal(self):
        for bad in ("", "SPEC", "SPEC x", "SPEC 99 PRO", "ITEM 42 EXEC", "OPS 3 PRO",
                    "ITEM 1 PRO", "launch", "SPEC 1 EXEC"):
            c = pc.parse(bad)
            assert c.kind == "illegal" and c.echo == pc.ILLEGAL_ENTRY, bad

    def test_help_lists_everything(self):
        lines = "\n".join(pc.help_lines())
        for key in ("SPEC 1", "SPEC 67", "ITEM 1 EXEC", "ITEM 5 EXEC", "ITEM 6 EXEC", "OPS 9 PRO"):
            assert key in lines
