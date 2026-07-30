"""Build first-party native test tools once per Python test process."""

from functools import lru_cache
from pathlib import Path
import shutil
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]
_BUILD_ROOT = tempfile.TemporaryDirectory(prefix="emuflow-native-tests-")


@lru_cache(maxsize=1)
def tlr_router() -> Path:
    compiler = (
        shutil.which("c++")
        or shutil.which("g++")
        or shutil.which("clang++")
    )
    if compiler is None:
        raise RuntimeError("a C++17 compiler is required for routing tests")
    executable = Path(_BUILD_ROOT.name) / "emuflow_tlr_router"
    subprocess.run(
        [
            compiler,
            "-std=c++17",
            "-O2",
            str(ROOT / "src" / "native" / "tlr_router.cpp"),
            "-o",
            str(executable),
        ],
        check=True,
    )
    return executable


@lru_cache(maxsize=1)
def vtr_architecture_importer() -> Path:
    compiler = (
        shutil.which("c++")
        or shutil.which("g++")
        or shutil.which("clang++")
    )
    if compiler is None:
        raise RuntimeError(
            "a C++17 compiler is required for VTR architecture tests"
        )
    executable = Path(_BUILD_ROOT.name) / "emuflow_vtr_arch_importer"
    pugixml = ROOT / "engines/openparf/thirdparty/pugixml/src"
    subprocess.run(
        [
            compiler,
            "-std=c++17",
            "-O2",
            f"-I{pugixml}",
            str(ROOT / "src/native/vtr_architecture_importer.cpp"),
            str(pugixml / "pugixml.cpp"),
            "-o",
            str(executable),
        ],
        check=True,
    )
    return executable
