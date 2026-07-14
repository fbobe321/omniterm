"""Background file-transfer worker for the SFTP browser.

Runs a list of get/put jobs off the UI thread and reports progress. For remote
transfers it opens a DEDICATED SFTP channel from the SSH transport, so the
browser's own SFTP client (used on the main thread for listing) is never touched
concurrently — paramiko's SFTPClient is not safe for concurrent use.
"""
from PyQt6.QtCore import QThread, pyqtSignal


class _Cancelled(Exception):
    pass


class TransferWorker(QThread):
    # done_bytes, total_bytes, current file name
    progress = pyqtSignal(int, int, str)
    # ok_count, list of error strings ("Cancelled" if the user aborted)
    finished_all = pyqtSignal(int, list)

    def __init__(self, jobs, transport=None, local_adapter=None, parent=None):
        """jobs: list of dicts {kind: 'download'|'upload', src, dst, size, name}.
        Provide transport (paramiko Transport) for remote transfers, or
        local_adapter (LocalFSAdapter) for local ones."""
        super().__init__(parent)
        self._jobs = jobs
        self._transport = transport
        self._local_adapter = local_adapter
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        opened = False
        try:
            if self._transport is not None:
                import paramiko
                sftp = paramiko.SFTPClient.from_transport(self._transport)
                opened = True
            else:
                sftp = self._local_adapter
        except Exception as e:  # noqa: BLE001
            self.finished_all.emit(0, [f"Could not open transfer channel: {e}"])
            return

        total = sum(max(0, j.get("size", 0)) for j in self._jobs)
        done_base = 0
        ok = 0
        errors = []
        for j in self._jobs:
            name = j["name"]
            base = done_base

            def cb(transferred, _total, base=base, name=name):
                if self._cancel:
                    raise _Cancelled()
                self.progress.emit(base + transferred, total, name)

            try:
                if self._cancel:
                    raise _Cancelled()
                if j["kind"] == "download":
                    sftp.get(j["src"], j["dst"], callback=cb)
                else:
                    sftp.put(j["src"], j["dst"], callback=cb)
                ok += 1
            except _Cancelled:
                errors.append("Cancelled")
                break
            except Exception as e:  # noqa: BLE001
                errors.append(f"{name}: {e}")
            done_base += max(0, j.get("size", 0))
            self.progress.emit(done_base, total, name)

        if opened:
            try:
                sftp.close()
            except Exception:
                pass
        self.finished_all.emit(ok, errors)
