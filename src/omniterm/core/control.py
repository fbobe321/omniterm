"""Control-socket transport shared by the GUI server and the CLI client.

This module is intentionally Qt-free so `omniterm-cli` can talk to a running GUI
without importing PyQt. The GUI side (`core/control_server.py`) builds on these
helpers; the CLI side calls `send_command`.

Protocol: newline-delimited JSON over a localhost TCP socket. The running GUI
writes a control file (`~/.omniterm_ctl.json`, mode 0600) containing the port
and a random token; clients must present the token on every request.
"""
import json
import os
import socket


class ControlError(Exception):
    """No running instance, connection refused, or a server-side error."""


def control_file_path():
    return os.environ.get("OMNITERM_CTL_FILE") or \
        os.path.join(os.path.expanduser("~"), ".omniterm_ctl.json")


def write_control_file(port, token):
    path = control_file_path()
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"port": port, "token": token}, f)
    try:
        os.chmod(path, 0o600)   # only the owner may read the token
    except OSError:
        pass


def read_control_file():
    try:
        with open(control_file_path(), "r", encoding="utf-8") as f:
            return json.load(f)
    except (IOError, OSError, ValueError):
        return None


def remove_control_file():
    try:
        os.remove(control_file_path())
    except OSError:
        pass


def recv_line(sock, limit=50_000_000):
    """Read one newline-terminated message from a socket."""
    buf = bytearray()
    while b"\n" not in buf:
        chunk = sock.recv(65536)
        if not chunk:
            break
        buf += chunk
        if len(buf) > limit:
            break
    line, _, _ = bytes(buf).partition(b"\n")
    return line.decode("utf-8", "replace")


def send_json(sock, obj):
    sock.sendall((json.dumps(obj) + "\n").encode("utf-8"))


def send_command(cmd, args=None, timeout=30.0):
    """Client: send one command to the running GUI and return its JSON reply."""
    info = read_control_file()
    if not info:
        raise ControlError("OmniTerm does not appear to be running "
                           "(no control socket). Start the GUI first.")
    try:
        sock = socket.create_connection(("127.0.0.1", info["port"]), timeout=timeout)
    except OSError as e:
        raise ControlError(f"could not reach the running OmniTerm: {e}")
    try:
        send_json(sock, {"token": info.get("token"), "cmd": cmd, "args": args or {}})
        reply = recv_line(sock)
    finally:
        sock.close()
    if not reply:
        raise ControlError("no response from OmniTerm")
    try:
        return json.loads(reply)
    except ValueError:
        raise ControlError("malformed response from OmniTerm")
