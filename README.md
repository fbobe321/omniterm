# OmniTerm

A cross-platform, [MobaXterm](https://mobaxterm.mobatek.net/)-style terminal built with PyQt6.
OmniTerm gives you SSH, serial, and local shell sessions in a tabbed interface, an
integrated SFTP file browser, encrypted credential storage, and a dark theme out of the box.

## Features

- **Multiple session types** — SSH (password or key auth), serial (configurable
  baud / data bits / parity / stop bits), and local PTY shells.
- **Tabbed sessions** with a sidebar session tree (folders supported).
- **Integrated SFTP browser** that attaches automatically to SSH sessions for
  upload / download.
- **Encrypted credentials** — passwords are stored with Fernet encryption,
  optionally protected by a master password (PBKDF2-HMAC-SHA256).
- **xterm.js terminal** rendered via Qt WebEngine for accurate ANSI handling.
- **Configurable home directory** and an optional shared sessions file.

## Installation

```bash
pip install omniterm
```

On Linux you may also need the system Qt WebEngine runtime libraries provided by
your distribution.

## Usage

After installing, launch from the command line:

```bash
omniterm
```

From a checkout:

```bash
pip install -e .
omniterm
```

## Development

```bash
git clone https://github.com/fbobe321/omniterm
cd omniterm
pip install -e .
python -m omniterm.main
```

## License

MIT — see [LICENSE](LICENSE).
