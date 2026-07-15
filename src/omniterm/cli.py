"""Headless command-line interface for OmniTerm.

Drive OmniTerm's capabilities — saved sessions, remote command execution, and
SFTP transfers — from scripts and agents WITHOUT the GUI. Commands are
deterministic and support ``--json`` for machine consumption. There is also a
stateful ``repl`` mode.

Entry points:  ``omniterm-cli``  or  ``python -m omniterm.cli``

Examples:
    omniterm-cli session list --json
    omniterm-cli exec myserver "systemctl is-active nginx" --json
    omniterm-cli sftp put myserver ./build.tar.gz /tmp/
    omniterm-cli sftp ls myserver /var/log --json
"""
import argparse
import json
import os
import shlex
import stat as statmod
import sys

from omniterm.core import config
from omniterm.core import control


# --------------------------------------------------------------------------- #
# Session helpers (reused by every command)
# --------------------------------------------------------------------------- #
def iter_sessions(sessions=None):
    """Yield every non-folder session, flattening nested folders."""
    if sessions is None:
        sessions = config.load_sessions().get("sessions", [])
    for s in sessions:
        if s.get("type") == "folder":
            yield from iter_sessions(s.get("children", []))
        else:
            yield s


def resolve_session(name_or_id):
    """Find a session by exact name or id, or None."""
    for s in iter_sessions():
        if s.get("id") == name_or_id or s.get("name") == name_or_id:
            return s
    return None


def require_session(name_or_id, want_type=None):
    s = resolve_session(name_or_id)
    if s is None:
        raise CliError(f"no session named '{name_or_id}' "
                       f"(see: omniterm-cli session list)")
    if want_type and s.get("type") != want_type:
        raise CliError(f"session '{name_or_id}' is type '{s.get('type')}', "
                       f"not '{want_type}'")
    return s


class CliError(Exception):
    """A user-facing error (bad args, missing session, connection failure)."""


# --------------------------------------------------------------------------- #
# SSH / SFTP (headless, via paramiko — mirrors core/ssh_client auth)
# --------------------------------------------------------------------------- #
def connect_ssh(session, timeout=10):
    import paramiko
    if session.get("type") != "ssh":
        raise CliError(f"session '{session.get('name')}' is not an SSH session")
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    host, user = session.get("host"), session.get("user")
    port = session.get("port", 22)
    try:
        if session.get("auth_method", "key") == "key":
            client.connect(host, port=port, username=user,
                           key_filename=session.get("key_path"), timeout=timeout)
        else:
            pw = config.decrypt_password(session.get("password", ""))
            client.connect(host, port=port, username=user, password=pw, timeout=timeout)
    except Exception as e:  # noqa: BLE001
        raise CliError(f"could not connect to {user}@{host}:{port}: {e}")
    return client


def run_exec(session, command, timeout=None):
    client = connect_ssh(session)
    try:
        _stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
        out = stdout.read().decode("utf-8", "replace")
        err = stderr.read().decode("utf-8", "replace")
        code = stdout.channel.recv_exit_status()
        return {"stdout": out, "stderr": err, "exit_code": code}
    finally:
        client.close()


def sftp_ls(session, path):
    client = connect_ssh(session)
    try:
        sftp = client.open_sftp()
        rows = []
        for a in sftp.listdir_attr(path):
            rows.append({
                "name": a.filename,
                "size": a.st_size,
                "is_dir": statmod.S_ISDIR(a.st_mode),
                "mtime": a.st_mtime,
            })
        rows.sort(key=lambda r: (not r["is_dir"], r["name"].lower()))
        return rows
    finally:
        client.close()


def _progress_printer():
    def cb(done, total):
        if total:
            pct = int(done * 100 / total)
            sys.stderr.write(f"\r  {pct:3d}%  ({done}/{total} bytes)")
            sys.stderr.flush()
            if done >= total:
                sys.stderr.write("\n")
    return cb


def sftp_get(session, remote, local, progress=False):
    client = connect_ssh(session)
    try:
        sftp = client.open_sftp()
        if os.path.isdir(local):
            local = os.path.join(local, os.path.basename(remote))
        sftp.get(remote, local, callback=_progress_printer() if progress else None)
        return {"remote": remote, "local": local}
    finally:
        client.close()


def sftp_put(session, local, remote, progress=False):
    client = connect_ssh(session)
    try:
        sftp = client.open_sftp()
        # If remote is an existing directory, keep the local basename.
        try:
            if statmod.S_ISDIR(sftp.stat(remote).st_mode):
                remote = remote.rstrip("/") + "/" + os.path.basename(local)
        except IOError:
            pass
        sftp.put(local, remote, callback=_progress_printer() if progress else None)
        return {"local": local, "remote": remote}
    finally:
        client.close()


# --------------------------------------------------------------------------- #
# Output helpers
# --------------------------------------------------------------------------- #
def _emit(obj, as_json):
    if as_json:
        print(json.dumps(obj, indent=2, default=str))
        return
    if isinstance(obj, str):
        print(obj)
    else:
        print(json.dumps(obj, indent=2, default=str))


def _fmt_size(n):
    size = float(n or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{int(size)}{unit}" if unit == "B" else f"{size:.1f}{unit}"
        size /= 1024


# --------------------------------------------------------------------------- #
# Command handlers
# --------------------------------------------------------------------------- #
def cmd_session_list(args):
    rows = [{"name": s.get("name"), "type": s.get("type"),
             "host": s.get("host"), "user": s.get("user"),
             "port": s.get("port")} for s in iter_sessions()]
    if args.json:
        _emit(rows, True)
    elif not rows:
        print("(no sessions)")
    else:
        for r in rows:
            loc = f"{r['user']}@{r['host']}" if r.get("host") else ""
            print(f"{r['name']:<24} {r['type']:<7} {loc}")


def cmd_session_show(args):
    s = require_session(args.name)
    safe = {k: v for k, v in s.items() if k != "password"}
    safe["has_password"] = bool(s.get("password"))
    _emit(safe, args.json)


def cmd_session_add(args):
    if resolve_session(args.name):
        raise CliError(f"a session named '{args.name}' already exists")
    import uuid
    session = {"id": str(uuid.uuid4()), "name": args.name, "type": "ssh",
               "host": args.host, "user": args.user, "port": args.port,
               "auth_method": args.auth}
    if args.auth == "password":
        pw = args.password
        if pw is None and args.ask_password:
            import getpass
            pw = getpass.getpass("Password: ")
        if not pw:
            raise CliError("password auth requires --password or --ask-password")
        session["password"] = config.encrypt_password(pw)
    else:
        if not args.key_path:
            raise CliError("key auth requires --key-path")
        session["key_path"] = args.key_path
    data = config.load_sessions()
    data.setdefault("sessions", []).append(session)
    config.save_sessions(data)
    _emit({"added": args.name}, args.json) if args.json else print(f"Added session '{args.name}'.")


def cmd_session_remove(args):
    target = require_session(args.name)
    data = config.load_sessions()

    def prune(lst):
        out = []
        for s in lst:
            if s.get("id") == target.get("id"):
                continue
            if s.get("type") == "folder":
                s["children"] = prune(s.get("children", []))
            out.append(s)
        return out

    data["sessions"] = prune(data.get("sessions", []))
    config.save_sessions(data)
    _emit({"removed": args.name}, args.json) if args.json else print(f"Removed session '{args.name}'.")


def cmd_exec(args):
    s = require_session(args.session, want_type="ssh")
    result = run_exec(s, args.command, timeout=args.timeout)
    if args.json:
        _emit(result, True)
    else:
        if result["stdout"]:
            sys.stdout.write(result["stdout"])
        if result["stderr"]:
            sys.stderr.write(result["stderr"])
    return result["exit_code"]


def cmd_sftp_ls(args):
    s = require_session(args.session, want_type="ssh")
    rows = sftp_ls(s, args.path)
    if args.json:
        _emit(rows, True)
    else:
        for r in rows:
            kind = "d" if r["is_dir"] else "-"
            print(f"{kind} {_fmt_size(r['size']):>8}  {r['name']}")


def cmd_sftp_get(args):
    s = require_session(args.session, want_type="ssh")
    res = sftp_get(s, args.remote, args.local or ".", progress=not args.json)
    _emit(res, args.json) if args.json else print(f"Downloaded → {res['local']}")


def cmd_sftp_put(args):
    s = require_session(args.session, want_type="ssh")
    res = sftp_put(s, args.local, args.remote, progress=not args.json)
    _emit(res, args.json) if args.json else print(f"Uploaded → {res['remote']}")


def _ctl(cmd, args):
    try:
        resp = control.send_command(cmd, args)
    except control.ControlError as e:
        raise CliError(str(e))
    if not resp.get("ok", False):
        raise CliError(resp.get("error", "control command failed"))
    return resp


def cmd_ctl_ping(args):
    r = _ctl("ping", {})
    if args.json:
        _emit({k: v for k, v in r.items() if k != "ok"}, True)
    else:
        print(f"OmniTerm {r.get('version')} — {r.get('tabs')} tab(s) open")


def cmd_ctl_list(args):
    r = _ctl("list-tabs", {})
    if args.json:
        _emit(r["tabs"], True)
    else:
        for t in r["tabs"]:
            extra = t.get("session") or t.get("type") or ""
            print(f"[{t['index']}] {t['title']}  {extra}")


def cmd_ctl_open(args):
    a = {"type": args.type}
    if args.type == "ssh":
        a["session"] = args.session
    r = _ctl("open", a)
    _emit({"index": r["index"]}, True) if args.json else print(f"Opened tab {r['index']}")


def _pane_args(args, base):
    if getattr(args, "pane", None) is not None:
        base["pane"] = args.pane
    return base


def cmd_ctl_send_keys(args):
    r = _ctl("send-keys", _pane_args(args, {"tab": args.tab, "text": args.text,
                                            "enter": args.enter}))
    _emit(r, True) if args.json else print(f"Sent to tab {args.tab}")


def cmd_ctl_run(args):
    r = _ctl("run", _pane_args(args, {"tab": args.tab, "text": args.text}))
    _emit(r, True) if args.json else print(f"Ran on tab {args.tab}")


def cmd_ctl_capture(args):
    r = _ctl("capture", _pane_args(args, {"tab": args.tab,
                                          "scrollback": args.scrollback}))
    _emit({"text": r["text"]}, True) if args.json else print(r["text"])


def cmd_ctl_focus(args):
    r = _ctl("focus-tab", _pane_args(args, {"tab": args.tab}))
    _emit(r, True) if args.json else print(f"Focused tab {args.tab}")


def cmd_ctl_close(args):
    _ctl("close-tab", {"tab": args.tab})
    _emit({"closed": args.tab}, True) if args.json else print(f"Closed tab {args.tab}")


def cmd_ctl_split(args):
    try:
        tabs = [int(x) for x in args.tabs.split(",") if x.strip() != ""]
    except ValueError:
        raise CliError("--tabs must be a comma-separated list of tab indices, e.g. 0,1")
    r = _ctl("split", {"tabs": tabs, "orientation": args.orientation})
    _emit(r, True) if args.json else \
        print(f"Split into tab {r['index']} ({r['panes']} panes)")


def cmd_ctl_unsplit(args):
    r = _ctl("unsplit", {"tab": args.tab})
    _emit(r, True) if args.json else \
        print(f"Unsplit tab {args.tab} into {r['panes']} tabs")


def cmd_repl(args):
    print("OmniTerm CLI — interactive mode. Type 'help' or 'quit'.")
    parser = build_parser()
    while True:
        try:
            line = input("omniterm> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line:
            continue
        if line in ("quit", "exit"):
            break
        if line == "help":
            parser.print_help()
            continue
        try:
            sub = parser.parse_args(shlex.split(line))
        except SystemExit:
            continue  # argparse already printed the error/usage
        if getattr(sub, "func", None) in (None, cmd_repl):
            print("Unknown command. Type 'help'.")
            continue
        try:
            sub.func(sub)
        except CliError as e:
            print(f"Error: {e}", file=sys.stderr)


# --------------------------------------------------------------------------- #
# Parser
# --------------------------------------------------------------------------- #
def build_parser():
    p = argparse.ArgumentParser(
        prog="omniterm-cli",
        description="Headless control of OmniTerm: sessions, remote exec, SFTP.")
    sub = p.add_subparsers(dest="command")

    # session ...
    sp = sub.add_parser("session", help="manage saved sessions")
    ssub = sp.add_subparsers(dest="subcommand")
    s_list = ssub.add_parser("list", help="list sessions")
    s_list.add_argument("--json", action="store_true")
    s_list.set_defaults(func=cmd_session_list)
    s_show = ssub.add_parser("show", help="show one session (password redacted)")
    s_show.add_argument("name")
    s_show.add_argument("--json", action="store_true")
    s_show.set_defaults(func=cmd_session_show)
    s_add = ssub.add_parser("add", help="add an SSH session")
    s_add.add_argument("name")
    s_add.add_argument("--host", required=True)
    s_add.add_argument("--user", required=True)
    s_add.add_argument("--port", type=int, default=22)
    s_add.add_argument("--auth", choices=["password", "key"], default="key")
    s_add.add_argument("--password")
    s_add.add_argument("--ask-password", action="store_true")
    s_add.add_argument("--key-path")
    s_add.add_argument("--json", action="store_true")
    s_add.set_defaults(func=cmd_session_add)
    s_rm = ssub.add_parser("remove", help="remove a session")
    s_rm.add_argument("name")
    s_rm.add_argument("--json", action="store_true")
    s_rm.set_defaults(func=cmd_session_remove)

    # exec ...
    ex = sub.add_parser("exec", help="run a command over SSH and capture output")
    ex.add_argument("session")
    ex.add_argument("command")
    ex.add_argument("--timeout", type=float, default=None)
    ex.add_argument("--json", action="store_true")
    ex.set_defaults(func=cmd_exec)

    # sftp ...
    sf = sub.add_parser("sftp", help="SFTP file operations")
    sfsub = sf.add_subparsers(dest="subcommand")
    f_ls = sfsub.add_parser("ls", help="list a remote directory")
    f_ls.add_argument("session")
    f_ls.add_argument("path")
    f_ls.add_argument("--json", action="store_true")
    f_ls.set_defaults(func=cmd_sftp_ls)
    f_get = sfsub.add_parser("get", help="download a remote file")
    f_get.add_argument("session")
    f_get.add_argument("remote")
    f_get.add_argument("local", nargs="?", default=".")
    f_get.add_argument("--json", action="store_true")
    f_get.set_defaults(func=cmd_sftp_get)
    f_put = sfsub.add_parser("put", help="upload a local file")
    f_put.add_argument("session")
    f_put.add_argument("local")
    f_put.add_argument("remote")
    f_put.add_argument("--json", action="store_true")
    f_put.set_defaults(func=cmd_sftp_put)

    # ctl ...  (drive a running GUI over the control socket)
    ct = sub.add_parser("ctl", help="control a running OmniTerm GUI")
    ctsub = ct.add_subparsers(dest="subcommand")
    c_ping = ctsub.add_parser("ping", help="check the running instance")
    c_ping.add_argument("--json", action="store_true")
    c_ping.set_defaults(func=cmd_ctl_ping)
    c_list = ctsub.add_parser("list-tabs", help="list open tabs")
    c_list.add_argument("--json", action="store_true")
    c_list.set_defaults(func=cmd_ctl_list)
    c_open = ctsub.add_parser("open", help="open a new tab")
    c_open.add_argument("--type", choices=["local", "home", "ssh"], default="local")
    c_open.add_argument("--session", help="saved session name (for --type ssh)")
    c_open.add_argument("--json", action="store_true")
    c_open.set_defaults(func=cmd_ctl_open)
    c_sk = ctsub.add_parser("send-keys", help="send text to a tab (or pane)")
    c_sk.add_argument("--tab", type=int, required=True)
    c_sk.add_argument("--pane", type=int, help="pane index within a split tab")
    c_sk.add_argument("--text", required=True)
    c_sk.add_argument("--enter", action="store_true", help="append Enter")
    c_sk.add_argument("--json", action="store_true")
    c_sk.set_defaults(func=cmd_ctl_send_keys)
    c_run = ctsub.add_parser("run", help="send a command line + Enter to a tab/pane")
    c_run.add_argument("--tab", type=int, required=True)
    c_run.add_argument("--pane", type=int, help="pane index within a split tab")
    c_run.add_argument("--text", required=True)
    c_run.add_argument("--json", action="store_true")
    c_run.set_defaults(func=cmd_ctl_run)
    c_cap = ctsub.add_parser("capture", help="read a tab's (or pane's) visible text")
    c_cap.add_argument("--tab", type=int, required=True)
    c_cap.add_argument("--pane", type=int, help="pane index within a split tab")
    c_cap.add_argument("--scrollback", type=int, default=0)
    c_cap.add_argument("--json", action="store_true")
    c_cap.set_defaults(func=cmd_ctl_capture)
    c_focus = ctsub.add_parser("focus-tab", help="switch to a tab (or focus a pane)")
    c_focus.add_argument("--tab", type=int, required=True)
    c_focus.add_argument("--pane", type=int, help="pane index within a split tab")
    c_focus.add_argument("--json", action="store_true")
    c_focus.set_defaults(func=cmd_ctl_focus)
    c_close = ctsub.add_parser("close-tab", help="close a tab")
    c_close.add_argument("--tab", type=int, required=True)
    c_close.add_argument("--json", action="store_true")
    c_close.set_defaults(func=cmd_ctl_close)
    c_split = ctsub.add_parser("split", help="combine open tabs into a split view")
    c_split.add_argument("--tabs", required=True,
                         help="comma-separated tab indices to combine, e.g. 0,1")
    c_split.add_argument("--orientation", choices=["horizontal", "vertical"],
                         default="horizontal")
    c_split.add_argument("--json", action="store_true")
    c_split.set_defaults(func=cmd_ctl_split)
    c_unsplit = ctsub.add_parser("unsplit", help="split a combined tab back into tabs")
    c_unsplit.add_argument("--tab", type=int, required=True)
    c_unsplit.add_argument("--json", action="store_true")
    c_unsplit.set_defaults(func=cmd_ctl_unsplit)

    # repl
    rp = sub.add_parser("repl", help="interactive shell")
    rp.set_defaults(func=cmd_repl)

    return p


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 2
    try:
        rc = args.func(args)
        return int(rc) if isinstance(rc, int) else 0
    except CliError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
