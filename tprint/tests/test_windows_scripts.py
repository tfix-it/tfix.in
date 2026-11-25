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
