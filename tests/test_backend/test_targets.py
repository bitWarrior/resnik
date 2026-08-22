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
    def test_vector_emits_compile_error(self, target: str) -> None:
        ast = _vector_prog()
        code = RustCodeGenerator(target=target).generate(ast)
        assert "compile_error!" in code
        assert "VECTOR/MATRIX" in code
        host = RustCodeGenerator(target="host").generate(ast)
        assert "compile_error!" not in host

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
