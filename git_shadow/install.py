"""Install lightweight local CLI wrappers without modifying Python globally."""

from __future__ import annotations

import os
import pathlib
import shlex
import sys
from typing import List


def install_wrappers(destination: str) -> List[pathlib.Path]:
    target = pathlib.Path(destination).expanduser().resolve()
    target.mkdir(parents=True, exist_ok=True)
    source_root = pathlib.Path(__file__).resolve().parent.parent
    python = os.environ.get("GIT_SHADOW_PYTHON") or sys.executable or "python3"
    root_literal = shlex.quote(str(source_root))
    unix_wrapper = target / "git-shadow"
    unix_wrapper.write_text(
        "#!/bin/sh\n"
        "set -eu\n"
        "export PYTHONPATH=%s${PYTHONPATH:+:$PYTHONPATH}\n"
        "exec %s -m git_shadow.cli \"$@\"\n" % (root_literal, shlex.quote(python)),
        encoding="utf-8",
    )
    unix_wrapper.chmod(0o755)

    windows_wrapper = target / "git-shadow.cmd"
    windows_root = str(source_root).replace("%", "%%")
    windows_wrapper.write_text(
        "@echo off\r\n"
        "set \"PYTHONPATH=%s;%%PYTHONPATH%%\"\r\n"
        "\"%s\" -m git_shadow.cli %%*\r\n" % (windows_root, python),
        encoding="utf-8",
    )
    return [unix_wrapper, windows_wrapper]
