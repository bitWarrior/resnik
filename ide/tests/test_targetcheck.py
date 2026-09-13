"""targetcheck.target_issues — surface no_std compile_error! features early."""

from resnik.driver import check

from resnik_ide.targetcheck import target_issues

VEC = "T: PROGRAM;\n  DECLARE V VECTOR(3);\n  V = V * 2;\nCLOSE T;\n"
CHAR = "T: PROGRAM;\n  DECLARE S CHARACTER(8);\n  S = 'HI';\nCLOSE T;\n"
PLAIN = "T: PROGRAM;\n  DECLARE X INTEGER;\n  X = 1;\nCLOSE T;\n"
BAD = "T: PROGRAM;\n  DECLARE X INTEGER;\n  DECLARE X SCALAR;\nCLOSE T;\n"


def _check(tmp_path, src):
    p = tmp_path / "t.hal"
    p.write_text(src, encoding="utf-8")
    return check(p)


class TestTargetIssues:
    def test_host_never_reports(self, tmp_path):
        assert target_issues(_check(tmp_path, VEC), "host") == []

    def test_vector_on_esp32_c3_is_clean_since_libm(self, tmp_path):
        # no_std roadmap item 1: VECTOR/MATRIX lower via libm on embedded.
        assert target_issues(_check(tmp_path, VEC), "esp32-c3") == []

    def test_character_on_esp32_c3_is_reported(self, tmp_path):
        issues = target_issues(_check(tmp_path, CHAR), "esp32-c3")
        assert len(issues) == 1 and "CHARACTER" in issues[0]

    def test_plain_program_is_clean_on_embedded(self, tmp_path):
        assert target_issues(_check(tmp_path, PLAIN), "cortex-m") == []

    def test_failed_check_reports_nothing(self, tmp_path):
        res = _check(tmp_path, BAD)
        assert res.ok is False
        assert target_issues(res, "esp32-c3") == []

    def test_unknown_target(self, tmp_path):
        assert target_issues(_check(tmp_path, VEC), "z80") == []
