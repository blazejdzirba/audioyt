"""Backendy PTY: POSIX (stdlib `pty`) i Windows (ConPTY przez `pywinpty`).

Wspólny interfejs `PtyBackend` — widget terminala nie wie, na jakim systemie działa.
"""
from __future__ import annotations

import codecs
import errno
import os
import queue
import signal
import struct
import subprocess
import sys
import threading

from PySide6.QtCore import QObject, QSocketNotifier, Signal

IS_WINDOWS = sys.platform == "win32"


class PtyBackend(QObject):
    data_received = Signal(str)
    exited = Signal(int)

    def start(self, argv: list[str], cwd: str, env: dict[str, str], rows: int, cols: int) -> None:
        raise NotImplementedError

    def write(self, data: str) -> None:
        raise NotImplementedError

    def resize(self, rows: int, cols: int) -> None:
        raise NotImplementedError

    def terminate(self) -> None:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Linux / macOS
# ---------------------------------------------------------------------------
class PosixPty(PtyBackend):
    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._proc: subprocess.Popen | None = None
        self._fd = -1
        self._read_notifier: QSocketNotifier | None = None
        self._write_notifier: QSocketNotifier | None = None
        self._wbuf = bytearray()
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self._finished = False

    def start(self, argv, cwd, env, rows, cols) -> None:
        import fcntl
        import termios

        master, slave = os.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))

        def _become_controlling_tty() -> None:  # po setsid(), przed exec
            fcntl.ioctl(0, termios.TIOCSCTTY, 0)

        try:
            self._proc = subprocess.Popen(
                argv,
                stdin=slave,
                stdout=slave,
                stderr=slave,
                cwd=cwd,
                env=env,
                close_fds=True,
                start_new_session=True,
                preexec_fn=_become_controlling_tty,
            )
        except Exception:
            os.close(master)
            raise
        finally:
            os.close(slave)

        self._fd = master
        os.set_blocking(master, False)
        self._read_notifier = QSocketNotifier(master, QSocketNotifier.Type.Read, self)
        self._read_notifier.activated.connect(self._on_readable)

    # -- IO ---------------------------------------------------------------
    def _on_readable(self) -> None:
        while self._fd >= 0:
            try:
                chunk = os.read(self._fd, 65536)
            except BlockingIOError:
                return
            except OSError as exc:  # EIO = potomek zamknął PTY
                if exc.errno in (errno.EIO, errno.EBADF):
                    self._finish()
                    return
                raise
            if not chunk:
                self._finish()
                return
            text = self._decoder.decode(chunk)
            if text:
                self.data_received.emit(text)

    def write(self, data: str) -> None:
        if self._fd < 0 or not data:
            return
        self._wbuf += data.encode("utf-8")
        self._flush()

    def _flush(self) -> None:
        while self._wbuf and self._fd >= 0:
            try:
                n = os.write(self._fd, self._wbuf)
            except BlockingIOError:
                if self._write_notifier is None:
                    self._write_notifier = QSocketNotifier(
                        self._fd, QSocketNotifier.Type.Write, self
                    )
                    self._write_notifier.activated.connect(self._flush)
                self._write_notifier.setEnabled(True)
                return
            except OSError:
                self._wbuf.clear()
                return
            del self._wbuf[:n]
        if self._write_notifier is not None:
            self._write_notifier.setEnabled(False)

    def resize(self, rows: int, cols: int) -> None:
        if self._fd < 0:
            return
        import fcntl
        import termios

        fcntl.ioctl(self._fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))

    # -- zakończenie ------------------------------------------------------
    def _finish(self) -> None:
        if self._finished:
            return
        self._finished = True
        for n in (self._read_notifier, self._write_notifier):
            if n is not None:
                n.setEnabled(False)
        rc = -1
        if self._proc is not None:
            try:
                rc = self._proc.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                rc = -1
        self._close_fd()
        self.exited.emit(rc)

    def _close_fd(self) -> None:
        if self._fd >= 0:
            try:
                os.close(self._fd)
            except OSError:
                pass
            self._fd = -1

    def terminate(self) -> None:
        self._finished = True
        for n in (self._read_notifier, self._write_notifier):
            if n is not None:
                n.setEnabled(False)
        if self._proc is not None and self._proc.poll() is None:
            try:
                os.killpg(self._proc.pid, signal.SIGHUP)
            except (ProcessLookupError, PermissionError):
                pass
            try:
                self._proc.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(self._proc.pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass
        self._close_fd()


# ---------------------------------------------------------------------------
# Windows (ConPTY)
# ---------------------------------------------------------------------------
class WinPty(PtyBackend):
    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._proc = None
        self._wq: queue.Queue[str | None] = queue.Queue()
        self._closing = False

    def start(self, argv, cwd, env, rows, cols) -> None:
        from winpty import PtyProcess  # pywinpty

        self._proc = PtyProcess.spawn(argv, cwd=cwd, env=env, dimensions=(rows, cols))
        threading.Thread(target=self._reader, name="pty-reader", daemon=True).start()
        threading.Thread(target=self._writer, name="pty-writer", daemon=True).start()

    def _reader(self) -> None:
        proc = self._proc
        while not self._closing:
            try:
                data = proc.read(65536)
            except EOFError:
                break
            except Exception:
                break
            if data:
                self.data_received.emit(data)
        if not self._closing:
            code = getattr(proc, "exitstatus", None)
            self.exited.emit(int(code) if code is not None else 0)

    def _writer(self) -> None:
        # Osobny wątek: duże wklejki nie mogą blokować GUI.
        while True:
            data = self._wq.get()
            if data is None or self._closing:
                return
            try:
                self._proc.write(data)
            except Exception:
                return

    def write(self, data: str) -> None:
        if data and not self._closing:
            self._wq.put(data)

    def resize(self, rows: int, cols: int) -> None:
        if self._proc is not None:
            try:
                self._proc.setwinsize(rows, cols)
            except Exception:
                pass

    def terminate(self) -> None:
        self._closing = True
        self._wq.put(None)
        if self._proc is not None:
            try:
                self._proc.terminate(force=True)
            except Exception:
                pass


def create_backend(parent: QObject | None = None) -> PtyBackend:
    return WinPty(parent) if IS_WINDOWS else PosixPty(parent)