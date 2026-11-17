"""install.ps1 must run in the Windows PowerShell 5.1 that ships with Windows (bug 2).

CI runs the real install under powershell.exe 5.1 and pwsh 7. This test
catches the known 5.1 traps anywhere, without Windows:

* `*>` or `2>&1` on a native command with ErrorActionPreference = 'Stop'
  turns anything the program writes to stderr into a terminating
  NativeCommandError (the round-2 Test-Import crash);
* `&&`, `||`, ternaries and `??` are PowerShell 7 only;
* a file that isn't plain ASCII is read as the ANSI code page by 5.1;
* the progress bar makes Invoke-WebRequest many times slower in 5.1;
* `[CmdletBinding()]` can't be used in a script run through `irm | iex`;
* `Start-Process -Wait` on an installer that leaves a tray app running hangs.
"""
import re
from pathlib import Path

PS1 = Path(__file__).resolve().parents[1] / "install.ps1"


def code_lines():
    text = PS1.read_text(encoding="utf-8")
    text = re.sub(r"<#.*?#>", lambda m: "\n" * m.group(0).count("\n"), text, flags=re.S)  # block comments
    out = []
    for n, line in enumerate(text.splitlines(), 1):
        line = re.sub(r"'[^']*'", "''", line)                  # single-quoted strings
        line = re.sub(r'"[^"]*"', '""', line)                  # double-quoted strings
        line = line.split("#", 1)[0]
        out.append((n, line))
    return out


def test_plain_ascii():
    PS1.read_bytes().decode("ascii")


def test_no_ps7_only_operators():
    for n, line in code_lines():
        assert "&&" not in line and "||" not in line, f"line {n}: pipeline chain operator"
        assert "??" not in line, f"line {n}: null-coalescing operator"
        assert not re.search(r"\s\?\s.*\s:\s", line), f"line {n}: ternary operator"


def test_no_stream_redirection_of_native_commands():
    for n, line in code_lines():
        assert "*>" not in line, f"line {n}: *> redirect"
        assert "2>&1" not in line, f"line {n}: 2>&1 redirect"


def test_direct_native_calls_only_after_stop_is_lifted():
    lines = code_lines()
    raw = PS1.read_text().splitlines()
    lifted = None
    for n, line in lines:
        if re.search(r"\$ErrorActionPreference\s*=\s*''", line) and "'Continue'" in raw[n - 1]:
            lifted = n
        if re.match(r"\s*&\s*\$", line):
            assert lifted and lifted < n, f"line {n}: native call while ErrorActionPreference is Stop"


def test_progress_bar_off_and_no_cmdletbinding_and_no_tray_wait():
    text = PS1.read_text()
    assert "$ProgressPreference = 'SilentlyContinue'" in text
    assert "[CmdletBinding()]" not in text
    code = "\n".join(line for _, line in code_lines()).lower()
    assert "ollama" not in code and "-wait" not in code.replace("-passthru -wait -redirectstandarderror", "")


def _join_path_args(line, start):
    """Count positional arguments after one Join-Path (nested parentheses read as one)."""
    i, n, count = start, len(line), 0
    while i < n:
        while i < n and line[i] == " ":
            i += 1
        if i >= n or line[i] in ")}|;" or line[i] == "-":
            break
        if line[i] == "(":
            depth = 0
            while i < n:
                depth += line[i] == "("
                depth -= line[i] == ")"
                i += 1
                if depth == 0:
                    break
        else:
            while i < n and line[i] not in " )}|;":
                i += 1
        count += 1
    return count


def test_join_path_takes_two_arguments_in_51():
    for n, line in code_lines():
        for m in re.finditer(r"Join-Path\s", line):
            assert _join_path_args(line, m.end()) <= 2, f"line {n}: Join-Path with more than 2 parts"
