"""OS-owned device locks shared by every checkout, desktop and CLI process."""

import hashlib
from pathlib import Path
import sys
import tempfile


class DeviceLease:
    def __init__(self, serial, identity=None, directory=None):
        self.keys = sorted({f"serial:{serial}", identity or f"serial:{serial}"})
        self.directory = Path(directory or Path(tempfile.gettempdir()) / "gamebot-device-locks")
        self.handles = []

    def acquire(self):
        if self.handles:
            return self
        self.directory.mkdir(parents=True, exist_ok=True)
        try:
            for key in self.keys:
                path = self.directory / (hashlib.sha256(key.encode()).hexdigest() + ".lock")
                handle = path.open("a+b")
                self.handles.append(handle)
                if path.stat().st_size == 0:
                    handle.write(b"0")
                    handle.flush()
                handle.seek(0)
                try:
                    if sys.platform == "win32":
                        import msvcrt
                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError as exc:
                    raise RuntimeError("该模拟器已有 GameBot 任务运行，请先停止其他窗口或命令行任务") from exc
        except BaseException:
            self.release()
            raise
        return self

    def release(self):
        # Keep the file: deleting it allows another process to lock a new inode.
        while self.handles:
            self.handles.pop().close()

    def __enter__(self):
        return self.acquire()

    def __exit__(self, *_):
        self.release()
