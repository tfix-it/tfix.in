"""Shop PC 1 runs these with Windows PowerShell 5.1, which reads a .ps1 without a BOM as ANSI:
a UTF-8 '—' becomes bytes that include a curly double quote, which PowerShell treats as the
end of a double-quoted string -> the script fails to parse. Keep every script plain ASCII."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = sorted(ROOT.glob('windows/*.ps1')) + sorted(ROOT.glob('windows/*.bat')) + sorted(ROOT.glob('*.bat'))


def test_windows_scripts_are_plain_ascii():
    assert SCRIPTS
    for p in SCRIPTS:
        data = p.read_bytes()
        bad = [i for i, b in enumerate(data) if b > 127]
        assert not bad, f'{p.name} has non-ASCII bytes at {bad[:5]}'


def test_batch_files_use_crlf():
    for p in ROOT.glob('**/*.bat'):
        data = p.read_bytes()
        assert data.count(b'\n') == data.count(b'\r\n'), f'{p.name} must use CRLF line endings'


def test_one_click_keeps_shop_data():
    s = (ROOT / 'windows' / 'one-click.ps1').read_text()
    # updates copy only code folders, never the database, uploads, certificates or tprint.env
    assert "@('app', 'static', 'windows')" in s
    assert "'/XF', 'config.py'" in s
    for keep in ('printshop.db', 'uploads', 'tprint.env'):
        assert f"Remove-Item -LiteralPath (Join-Path $Root '{keep}')" not in s
    # braces and parentheses balance (cheap guard against a broken edit)
    assert s.count('{') == s.count('}') and s.count('(') == s.count(')')


def test_one_click_finds_sumatra_and_saves_it_in_tprint_env():
    s = (ROOT / 'windows' / 'one-click.ps1').read_text(encoding='ascii')
    assert "$env:LOCALAPPDATA\\SumatraPDF\\SumatraPDF.exe" in s      # the installer's default (per user)
    assert "Set-EnvLine $name $found" in s and 'Get-Printer' in s


def test_config_finds_tools_or_uses_the_env_setting(monkeypatch, tmp_path):
    import importlib
    from app import config
    exe = tmp_path / 'SumatraPDF.exe'
    exe.write_text('x')
    monkeypatch.setenv('SUMATRA_PATH', str(exe))
    assert importlib.reload(config).SUMATRA_PATH == str(exe)
    monkeypatch.delenv('SUMATRA_PATH')
    assert config._find_exe('NOPE_X', ['/no/such', str(exe)]) == str(exe)
    assert config._find_exe('NOPE_X', ['/no/a', '/no/b']) == '/no/a'
    importlib.reload(config)


def test_setup_script_is_balanced_and_never_prints_the_key():
    s = (ROOT / 'windows' / 'setup.ps1').read_text(encoding='ascii')
    assert s.count('{') == s.count('}') and s.count('(') == s.count(')')
    assert 'windows\\one-click.ps1' in s and "'SumatraPDF.SumatraPDF'" in s and 'Install-Python312' in s
    # the report on the Desktop must not contain the shop key: only "is set / not set"
    assert 'Say $envText' not in s and 'Write-Host $envText' not in s
    b = (ROOT / 'T-Print-Setup.bat').read_bytes()
    assert b'windows\\setup.ps1' in b and b'net session >nul' in b


def test_one_click_prefers_the_highest_zip_version():
    s = (ROOT / 'windows' / 'one-click.ps1').read_text(encoding='ascii')
    assert "-match '-v(\\d+)'" in s


def test_make_cert_writes_a_certificate(tmp_path):
    import subprocess, sys
    r = subprocess.run([sys.executable, str(ROOT / 'windows' / 'make-cert.py'), str(tmp_path)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert (tmp_path / 'cert.pem').read_text().startswith('-----BEGIN CERTIFICATE-----')
    assert 'PRIVATE KEY' in (tmp_path / 'key.pem').read_text()
    r2 = subprocess.run([sys.executable, str(ROOT / 'windows' / 'make-cert.py'), str(tmp_path)], capture_output=True, text=True)
    assert 'kept' in r2.stdout                      # never overwrites an existing certificate


def test_setup_new_folder_mode_moves_data_and_the_task():
    s = (ROOT / 'windows' / 'setup.ps1').read_text(encoding='ascii')
    assert '$HereMode' in s and "Unregister-ScheduledTask -TaskName 'T Print Server'" in s
    for f in ('printshop.db', 'tprint.env', 'cert.pem', 'key.pem'):
        assert f"'{f}'" in s
    assert 'Remove-Item -LiteralPath $Old' not in s          # the old folder is only copied from, never deleted
    assert "MOVED-TO-NEW-FOLDER.txt" in s and 'last-update.txt' in s


def test_venv_is_always_python_3_10_to_3_12():
    # pymupdf 1.24.10 and pywin32 306 have Windows wheels only up to cp312: Python 3.13/3.14
    # makes pip compile them and fail ("Unable to find Visual Studio"), as happened on Shop PC 1.
    fp = (ROOT / 'windows' / 'find-python.ps1').read_text(encoding='ascii')
    oc = (ROOT / 'windows' / 'one-click.ps1').read_text(encoding='ascii')
    st = (ROOT / 'windows' / 'setup.ps1').read_text(encoding='ascii')
    assert r"'^3\.(10|11|12)\.'" in fp and "@('-3.12')" in fp and 'py.exe install 3.12' in fp
    assert 'py.exe -3 -m venv' not in oc                      # never "newest Python"
    assert "find-python.ps1" in oc and "find-python.ps1" in st
    assert '--only-binary=pymupdf,pywin32' in oc               # never try to compile them
    assert 'import fastapi, uvicorn, fitz, img2pdf, win32print' in oc
    for s in (fp, oc, st):
        assert s.count('{') == s.count('}') and s.count('(') == s.count(')')


def test_env_template_is_ascii():
    # copied to tprint.env on Windows; a non-ASCII dash showed up as "???" there
    assert (ROOT / 'tprint.env.example').read_bytes().isascii()
