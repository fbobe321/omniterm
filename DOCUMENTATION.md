# 📘 OmniTerm: Technical Documentation

## 1. Introduction
**OmniTerm** is a high-security, local-first terminal multiplexer designed as a professional alternative to tools like MobaXterm. It provides a unified interface for SSH, Serial, and Local shell sessions, with a strict emphasis on **zero-telemetry**, **data sovereignty**, and **operational stability**.

### Core Objectives
- **Air-Gapped Design**: No external CDN dependencies; all assets (including the terminal renderer) are bundled locally.
- **Security First**: Local encryption of credentials using industry-standard cryptographic primitives.
- **High Performance**: Asynchronous I/O handling to ensure the GUI remains responsive even during high-frequency data bursts (e.g., serial telemetry).

---

## 2. System Architecture
OmniTerm employs a **Decoupled Worker Architecture**. The application is split into three distinct layers: the **Presentation Layer (UI)**, the **Communication Bridge**, and the **Execution Layer (Core Workers)**.

### 2.1 The Data Flow Path
**Input Flow (User $\rightarrow$ Remote):**
`User Keystroke` $\rightarrow$ `xterm.js (JS)` $\rightarrow$ `QWebChannel (Bridge)` $\rightarrow$ `PyBridge (Python)` $\rightarrow$ `Worker Thread` $\rightarrow$ `Protocol (SSH/Serial/PTY)` $\rightarrow$ `Remote Host`.

**Output Flow (Remote $\rightarrow$ User):**
`Remote Data` $\rightarrow$ `Worker Thread` $\rightarrow$ `PyQt Signal` $\rightarrow$ `TerminalTab` $\rightarrow$ `PyBridge` $\rightarrow$ `xterm.js (JS)` $\rightarrow$ `User Screen`.

### 2.2 Concurrency Model
To prevent the "Application Not Responding" (ANR) state common in network applications, OmniTerm uses **QThreads**. Every active session is encapsulated in its own worker thread. 
- **Isolation**: A crash or hang in an SSH connection cannot freeze the rest of the application.
- **Communication**: Workers communicate with the UI exclusively via `pyqtSignal`, ensuring that UI updates always happen on the Main Thread.

---

## 3. Module Deep-Dive

### 3.1 The Core Engine (`omniterm/core/`)

#### `config.py` (The Security Vault)
Handles the persistence of session data and the encryption of passwords.
- **Encryption Pipeline**: 
    1. **Salt**: A random 16-byte salt is stored in `.omniterm_salt`.
    2. **KDF**: Uses **PBKDF2HMAC** with SHA256 and 100,000 iterations to derive a key from the master password.
    3. **Symmetric Encryption**: Uses **Fernet (AES-128 CBC + HMAC)** to encrypt session passwords.
- **Session Storage**: Sessions are stored in a JSON format, supporting a recursive folder structure for organization.

#### `ssh_client.py` (The Network Layer)
A wrapper around the `paramiko` library.
- **Interactive Shell**: Uses `invoke_shell()` to request a pseudo-terminal (PTY) from the server, enabling full ANSI support for tools like `vim` or `htop`.
- **Macro Engine**: Implements a `send_macro` method that iterates through a list of commands and delays, executing them in a background thread to avoid blocking the worker's main read loop.

#### `serial_client.py` (The Hardware Layer)
A wrapper around `pyserial`.
- **The Buffering Strategy**: To prevent "Signal Flooding" (where the UI is overwhelmed by thousands of small serial updates), this module implements a **50ms aggregation buffer**. It collects all incoming bytes and emits them as a single block, maintaining 60FPS UI fluidity.

#### `local_pty.py` (The OS Layer)
Provides a native shell experience.
- **Cross-Platform PTY**: Uses `pywinpty` on Windows and the native `pty` module on Unix/Linux to spawn a shell process and capture its output.

### 3.2 The User Interface (`omniterm/ui/`)

#### `main_window.py` (The Orchestrator)
The central hub that manages the `QTabWidget` and coordinates between the `SessionDock` and the `TerminalTab`. It handles the global stylesheet and the lifecycle of all worker threads.

#### `terminal_tab.py` (The Hybrid Renderer)
The most complex UI component. It embeds a `QWebEngineView` to render `xterm.js`.
- **The Bridge**: Uses `QWebChannel` to expose a Python object (`PyBridge`) to the JavaScript environment.
- **Rendering**: Loads `index.html` from the local `static/` folder, ensuring no external network requests are made.

#### `session_dock.py` (The Navigator)
A `QTreeView` that organizes sessions. It uses a **Recursive Traversal Algorithm** to render nested folders from the session JSON, allowing for infinite levels of organization.

#### `sftp_browser.py` (The File Manager)
A remote file explorer using Paramiko's SFTP client.
- **Lazy Loading**: Directories are only fetched from the server when the user expands a node in the tree.
- **Connection Guard**: Implements a heartbeat check (`listdir('.')`) before every operation to detect dropped connections and prevent crashes.

---

## 4. Data Schema
Sessions are stored in `.omniterm_sessions.json` using the following structure:

```json
{
  "version": "1.0",
  "sessions": [
    {
      "type": "folder",
      "name": "Production",
      "children": [
        {
          "type": "ssh",
          "name": "Web-Server-01",
          "host": "10.0.0.1",
          "user": "admin",
          "password": "encrypted_token_here",
          "port": 22
        }
      ]
    },
    {
      "type": "serial",
      "name": "Console-Port",
      "com_port": "COM3",
      "baud_rate": 115200
    }
  ]
}
```

---

## 5. Extension Guide

### Adding a New Protocol
To add a new protocol (e.g., Telnet or Mosh):
1. **Create a Worker**: Create a new class in `core/` inheriting from `QThread`. Implement `data_received` and `error_occurred` signals.
2. **Update UI**: Add the protocol type to the `type_combo` in `main_window.py`'s `show_add_session_dialog`.
3. **Integrate**: Update `create_terminal_tab` in `main_window.py` to instantiate your new worker.

### Modifying the Terminal Appearance
The terminal's look is controlled by `static/xterm/index.html` and `xterm.css`. You can modify the CSS to change fonts, colors, and cursor styles without touching the Python code.

---

## 6. Installation & Requirements
### Dependencies
- **Python 3.10+**
- **PyQt6**: GUI Framework.
- **Paramiko**: SSH/SFTP implementation.
- **pySerial**: Serial port communication.
- **Cryptography**: AES/PBKDF2 implementation.
- **pywinpty**: Windows PTY support.

### Setup
```bash
pip install -r requirements.txt
python main.py
```
