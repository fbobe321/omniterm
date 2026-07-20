"""OmniTerm - cross-platform terminal with SSH, serial, and SFTP."""

def app_version():
    """Best-effort version string.

    A source checkout reads the adjacent pyproject.toml (authoritative while
    developing, even if an older wheel is also pip-installed); otherwise fall
    back to the installed package metadata; else 'dev'."""
    try:
        import re
        from pathlib import Path
        pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
        m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject.read_text(), re.M)
        if m:
            return m.group(1)
    except Exception:
        pass
    try:
        from importlib.metadata import version
        return version("omniterm")
    except Exception:
        pass
    return "dev"


__version__ = app_version()
