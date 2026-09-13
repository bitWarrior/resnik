"""TargetSpec / --target profile tests.

Cortex-M output must stay the stub. ESP32-C3 is the first silicon profile
(esp-hal, USB-serial WRITE, no STM32 memory.x).
"""

import pytest
from pathlib import Path
from resnik.backend.codegen import RustCodeGenerator
from resnik.backend.target_spec import TARGET_NAMES, TARGET_TRIPLES, lookup_target
from resnik.lexer import Lexer
from resnik.parser import Parser
from resnik.parser.ast_nodes import (
    CompoolNode,
    DeclareNode,
    LiteralNode,
    ProgramNode,
    TaskNode,
    WriteNode,
)
from resnik.types import DataType, TypeSpec

HELLO_WORLD_HAL = (
    Path(__file__).resolve().parents[2]
    / "docs"
    / "HAL-S-Source-Code-Examples"
    / "hello_word"
    / "hello_world.hal"
)


def _hello():
    return ProgramNode(
        name="P",
        statements=[
            WriteNode(
                channel=6,
                items=[LiteralNode(value="HI", literal_type="STRING")],
            )
        ],
    )


def _vector_prog():
    return ProgramNode(
        name="P",
        declarations=[
            DeclareNode(
                identifiers=["V"],
                type_spec=TypeSpec(data_type=DataType.VECTOR, dimensions=[3]),
            )
        ],
    )


class TestTargetSpec:
    def test_unknown_target_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown target"):
            RustCodeGenerator(target="nope")
        with pytest.raises(ValueError, match="Unknown target"):
            lookup_target("nope")

    def test_known_names_include_host_cortex_m_and_esp32_c3(self) -> None:
        assert "host" in TARGET_NAMES
        assert "cortex-m" in TARGET_NAMES
        assert "esp32-c3" in TARGET_NAMES
        assert TARGET_TRIPLES["cortex-m"] == "thumbv7em-none-eabihf"
        assert TARGET_TRIPLES["esp32-c3"] == "riscv32imc-unknown-none-elf"

    def test_generator_keeps_target_name(self) -> None:
        gen = RustCodeGenerator(target="cortex-m")
        assert gen.target == "cortex-m"
        assert gen.spec.name == "cortex-m"
        assert gen.spec.embedded is True

    def test_esp32_c3_spec_fields(self) -> None:
        gen = RustCodeGenerator(target="esp32-c3")
        assert gen.target == "esp32-c3"
        assert gen.spec.embedded is True
        assert gen.spec.entry == "esp-hal"
        assert gen.spec.write_sink == "esp-println"
        assert gen.spec.panic == "esp-backtrace"


class TestHostVsCortexMShape:
    def test_host_is_std(self) -> None:
        code = RustCodeGenerator(target="host").generate(_hello())
        assert "#![no_std]" not in code
        assert "#![no_main]" not in code
        assert "cortex_m_rt" not in code
        assert "esp_hal" not in code
        assert "fn main()" in code
        assert "fn main() -> !" not in code
        assert 'println!("{} {}", "HI")' in code or 'println!("{}", "HI")' in code

    def test_cortex_m_stub_shape_unchanged(self) -> None:
        code = RustCodeGenerator(target="cortex-m").generate(_hello())
        assert "#![no_std]" in code
        assert "#![no_main]" in code
        assert "use cortex_m_rt::entry;" in code
        assert "use panic_halt as _;" in code
        assert "use cortex_m_semihosting::hprintln;" in code
        assert "#[entry]" in code
        assert "fn main() -> ! {" in code
        assert "hprintln!" in code
        assert "println!" not in code.replace("hprintln!", "")
        assert "loop {}" in code
        assert "Target profile: cortex-m" in code
        assert "compile_error!" not in code
        assert "esp_hal" not in code

    def test_cortex_m_cargo_scaffold_unchanged(self, tmp_path: Path) -> None:
        gen = RustCodeGenerator(target="cortex-m")
        code = gen.generate(_hello())
        gen.emit_cargo_project(code, tmp_path, name="fw", build=False)
        toml = (tmp_path / "Cargo.toml").read_text()
        config = (tmp_path / ".cargo" / "config.toml").read_text()
        assert 'cortex-m-rt = "0.7"' in toml
        assert "thumbv7em-none-eabihf" in config
        assert (tmp_path / "memory.x").is_file()
        assert (tmp_path / "build.rs").is_file()
        assert "hprintln!" in (tmp_path / "src" / "main.rs").read_text()


class TestEsp32C3Shape:
    def test_esp32_c3_header_and_write(self) -> None:
        code = RustCodeGenerator(target="esp32-c3").generate(_hello())
        assert "#![no_std]" in code
        assert "#![no_main]" in code
        assert "use esp_hal::main;" in code
        assert "use esp_backtrace as _;" in code
        assert "use esp_println::println;" in code
        assert "esp_bootloader_esp_idf::esp_app_desc!()" in code
        assert "#[main]" in code
        assert "#[entry]" not in code
        assert "fn main() -> ! {" in code
        assert "esp_hal::init" in code
        assert "hprintln" not in code
        assert "cortex_m_rt" not in code
        assert "panic_halt" not in code
        assert 'println!("{}", "HI")' in code
        assert "loop {}" in code
        assert "Target profile: esp32-c3" in code
        assert "compile_error!" not in code

    def test_esp32_c3_cargo_scaffold(self, tmp_path: Path) -> None:
        gen = RustCodeGenerator(target="esp32-c3")
        code = gen.generate(_hello())
        gen.emit_cargo_project(code, tmp_path, name="fw", build=False)
        toml = (tmp_path / "Cargo.toml").read_text()
        config = (tmp_path / ".cargo" / "config.toml").read_text()
        main = (tmp_path / "src" / "main.rs").read_text()
        toolchain = (tmp_path / "rust-toolchain.toml").read_text()
        assert 'esp-hal = { version = "1.1", features = ["esp32c3"] }' in toml
        assert "esp-backtrace" in toml
        assert "esp-println" in toml
        assert "esp-bootloader-esp-idf" in toml
        assert "cortex-m-rt" not in toml
        assert "riscv32imc-unknown-none-elf" in config
        assert "linkall.x" in config
        assert "espflash flash --monitor" in config
        assert "force-frame-pointers" in config
        assert not (tmp_path / "memory.x").exists()
        assert not (tmp_path / "build.rs").exists()
        assert 'channel = "stable"' in toolchain
        assert "riscv32imc-unknown-none-elf" in toolchain
        assert "#[main]" in main
        assert "hprintln" not in main
        assert "esp_hal::init" in main

    def test_hello_world_hal_lowers_for_esp32_c3(self) -> None:
        src = HELLO_WORLD_HAL.read_text(encoding="utf-8")
        ast = Parser().parse(Lexer().tokenize(src, str(HELLO_WORLD_HAL)))
        code = RustCodeGenerator(target="esp32-c3").generate(ast)
        assert "HELLO, WORLD [HAL/S LIVES!]" in code
        assert "println!" in code
        assert "hprintln" not in code
        assert "compile_error!" not in code
        assert "#[main]" in code


class TestEmbeddedCompileError:
    @pytest.mark.parametrize("target", ["cortex-m", "esp32-c3"])
    def test_vector_no_longer_guarded(self, target: str) -> None:
        # no_std roadmap item 1: VECTOR/MATRIX lower via the libm-backed
        # prelude on embedded targets instead of tripping compile_error!.
        ast = _vector_prog()
        code = RustCodeGenerator(target=target).generate(ast)
        assert "compile_error!" not in code
        assert "mod hal_rt" in code and "trait FloatExt" in code
        host = RustCodeGenerator(target="host").generate(ast)
        assert "compile_error!" not in host and "FloatExt" not in host

    @pytest.mark.parametrize("target", ["cortex-m", "esp32-c3"])
    def test_task_emits_compile_error(self, target: str) -> None:
        ast = ProgramNode(
            name="P",
            tasks=[TaskNode(name="T", statements=[])],
        )
        code = RustCodeGenerator(target=target).generate(ast)
        assert "compile_error!" in code
        assert "SCHEDULE/TASK" in code

    @pytest.mark.parametrize("target", ["cortex-m", "esp32-c3"])
    def test_compool_emits_compile_error(self, target: str) -> None:
        ast = CompoolNode(
            name="SHARED",
            declarations=[
                DeclareNode(
                    identifiers=["X"],
                    type_spec=TypeSpec(data_type=DataType.INTEGER),
                )
            ],
        )
        code = RustCodeGenerator(target=target).generate(ast)
        assert "compile_error!" in code
        assert "COMPOOL" in code


class TestScaffoldOnce:
    """--emit-cargo must not clobber the user's crate: only src/main.rs is
    generated output; every other scaffold file is create-if-absent
    (--force-scaffold regenerates), and src/shim.rs is created once and
    never overwritten (docs/NO_STD_ROADMAP.md item 4)."""

    CODE = "#![no_std]\n#![no_main]\nfn body() {}\n"

    def _gen(self, target="esp32-c3"):
        from resnik.backend.codegen import RustCodeGenerator
        return RustCodeGenerator(target=target)

    def test_recompile_preserves_user_edits(self, tmp_path):
        gen = self._gen()
        r1 = gen.emit_cargo_project(self.CODE, tmp_path, name="fw")
        assert r1.kept_files == []
        toml = tmp_path / "Cargo.toml"
        toml.write_text(toml.read_text() + '\n# my dep\nlibm = "0.2"\n', encoding="utf-8")
        (tmp_path / "src" / "shim.rs").write_text("// mine\n", encoding="utf-8")
        r2 = gen.emit_cargo_project(self.CODE, tmp_path, name="fw")
        assert "# my dep" in toml.read_text()
        assert (tmp_path / "src" / "shim.rs").read_text() == "// mine\n"
        kept = sorted(p.rsplit("/", 1)[-1] for p in r2.kept_files)
        assert kept == ["Cargo.toml", "config.toml", "rust-toolchain.toml"]

    def test_force_scaffold_regenerates_but_never_shim(self, tmp_path):
        gen = self._gen()
        gen.emit_cargo_project(self.CODE, tmp_path, name="fw")
        (tmp_path / "Cargo.toml").write_text("# clobbered\n", encoding="utf-8")
        (tmp_path / "src" / "shim.rs").write_text("// mine\n", encoding="utf-8")
        r = gen.emit_cargo_project(self.CODE, tmp_path, name="fw", force_scaffold=True)
        assert r.kept_files == []
        assert "# clobbered" not in (tmp_path / "Cargo.toml").read_text()
        assert (tmp_path / "src" / "shim.rs").read_text() == "// mine\n"

    def test_main_rs_regenerated_with_mod_shim_after_inner_attrs(self, tmp_path):
        gen = self._gen()
        gen.emit_cargo_project(self.CODE, tmp_path, name="fw")
        lines = (tmp_path / "src" / "main.rs").read_text().splitlines()
        attrs = [i for i, ln in enumerate(lines) if ln.startswith("#![")]
        assert lines[max(attrs) + 1] == "mod shim;"
        assert lines.count("mod shim;") == 1
        # regenerating does not duplicate the injection
        gen.emit_cargo_project(self.CODE, tmp_path, name="fw")
        assert (tmp_path / "src" / "main.rs").read_text().count("mod shim;") == 1

    def test_shim_stub_documents_extern_c(self, tmp_path):
        gen = self._gen()
        gen.emit_cargo_project(self.CODE, tmp_path, name="fw")
        stub = (tmp_path / "src" / "shim.rs").read_text()
        assert 'extern "C"' in stub and "EXTERNAL" in stub
        assert "#![allow(dead_code" in stub

    def test_cortex_m_memory_x_preserved(self, tmp_path):
        gen = self._gen("cortex-m")
        gen.emit_cargo_project(self.CODE, tmp_path, name="fw")
        (tmp_path / "memory.x").write_text("/* my MCU map */\n", encoding="utf-8")
        r = gen.emit_cargo_project(self.CODE, tmp_path, name="fw")
        assert (tmp_path / "memory.x").read_text() == "/* my MCU map */\n"
        assert any(k.endswith("memory.x") for k in r.kept_files)

    def test_host_cargo_toml_kept(self, tmp_path):
        gen = self._gen("host")
        gen.emit_cargo_project("fn main() {}\n", tmp_path, name="app")
        (tmp_path / "Cargo.toml").write_text("# host edit\n", encoding="utf-8")
        r = gen.emit_cargo_project("fn main() {}\n", tmp_path, name="app")
        assert (tmp_path / "Cargo.toml").read_text() == "# host edit\n"
        assert any(k.endswith("Cargo.toml") for k in r.kept_files)
        # host scaffold has no shim machinery
        assert not (tmp_path / "src" / "shim.rs").exists()
        assert "mod shim;" not in (tmp_path / "src" / "main.rs").read_text()


class TestHprintlnReturnsUnit:
    """cortex-m-semihosting 0.5's hprintln! returns (), not Result — a
    trailing .ok() is a compile error on the scaffolded crate (caught by
    run_embedded_build.sh the first time the cortex-m case was built)."""

    def test_write_lowering_has_no_ok_call(self):
        from resnik import Lexer, Parser
        from resnik.backend.codegen import RustCodeGenerator
        src = "T: PROGRAM;\n  WRITE(6) 'HI', 42;\n  WRITE(6);\nCLOSE T;\n"
        ast = Parser().parse(Lexer().tokenize(src, "<t>"))
        code = RustCodeGenerator(target="cortex-m").generate(ast)
        assert 'hprintln!("{} {}", ' in code
        assert 'hprintln!("");' in code
        assert ".ok()" not in code


class TestEmbeddedFloatMath:
    """no_std roadmap item 1: VECTOR/MATRIX and scalar math intrinsics work
    on embedded targets — f32 math routes through a libm-backed FloatExt
    trait; hosted output keeps the inherent std methods and the std stubs."""

    VEC_SRC = (
        "K: PROGRAM;\n"
        "  DECLARE V VECTOR(3) INITIAL(3.0, 0.0, 4.0);\n"
        "  DECLARE M MATRIX(2,2) INITIAL(2.0, 0.0, 0.0, 2.0);\n"
        "  DECLARE S SCALAR;\n"
        "  S = ABVAL(V) + SQRT(2.25) + DETERM(M) + 2.0 ** 3;\n"
        "  WRITE(6) S, V;\n"
        "CLOSE K;\n"
    )

    def _code(self, src: str, target: str) -> str:
        from resnik import Lexer, Parser
        from resnik.backend.codegen import RustCodeGenerator
        p = Parser()
        ast = p.parse(Lexer().tokenize(src, "<t>"))
        assert not p.errors, p.errors
        return RustCodeGenerator(target=target).generate(ast)

    def test_vectors_no_longer_guarded_on_embedded(self):
        code = self._code(self.VEC_SRC, "esp32-c3")
        assert "compile_error" not in code
        assert "mod hal_rt" in code

    def test_embedded_prelude_is_core_only_with_floatext(self):
        code = self._code(self.VEC_SRC, "esp32-c3")
        assert "trait FloatExt" in code
        assert "libm::sqrtf" in code and "libm::powf" in code
        assert "use core::ops::" in code
        assert "std::" not in code            # the whole file must be core-clean
        assert "pub fn schedule(" not in code  # host stubs stay out

    def test_host_prelude_keeps_std_stubs_and_no_libm(self):
        code = self._code(self.VEC_SRC, "host")
        assert "pub fn schedule(" in code and "read_f32" in code
        assert "FloatExt" not in code and "libm" not in code

    def test_scalar_math_alone_pulls_floatext_on_embedded_only(self):
        src = "T: PROGRAM;\n  DECLARE X SCALAR;\n  X = SQRT(2.0) + SIN(1.0);\nCLOSE T;\n"
        emb = self._code(src, "esp32-c3")
        assert "trait FloatExt" in emb and "compile_error" not in emb
        host = self._code(src, "host")
        assert "mod hal_rt" not in host  # inherent std methods, no prelude

    def test_power_operator_pulls_floatext(self):
        src = "T: PROGRAM;\n  DECLARE X SCALAR;\n  X = 2.0 ** 0.5;\nCLOSE T;\n"
        assert "trait FloatExt" in self._code(src, "esp32-c3")

    def test_remaining_guards_are_precise(self):
        cases = {
            "T: PROGRAM;\n  DECLARE S CHARACTER(8);\nCLOSE T;\n":
                "CHARACTER variables need heap",
            "T: PROGRAM;\n  DECLARE X SCALAR;\n  READ(5) X;\nCLOSE T;\n":
                "READ needs a console",
            "T: PROGRAM;\n  WRITE(6) 'A' || 'B';\nCLOSE T;\n":
                "character concatenation",
        }
        for src, needle in cases.items():
            code = self._code(src, "esp32-c3")
            assert needle in code, (needle, src)
            # and the same source is unguarded on the host
            assert "compile_error" not in self._code(src, "host")

    def test_schedule_still_guarded_on_embedded(self):
        src = ("T: PROGRAM;\n  A: TASK;\n  CLOSE A;\n"
               "  SCHEDULE A PRIORITY(1);\nCLOSE T;\n")
        assert "SCHEDULE/TASK" in self._code(src, "esp32-c3")

    def test_embedded_scaffolds_carry_libm(self, tmp_path):
        from resnik.backend.codegen import RustCodeGenerator
        for target in ("esp32-c3", "cortex-m"):
            d = tmp_path / target
            RustCodeGenerator(target=target).emit_cargo_project(
                "#![no_std]\n#![no_main]\nfn b() {}\n", d, name="fw")
            assert 'libm = "0.2"' in (d / "Cargo.toml").read_text()
