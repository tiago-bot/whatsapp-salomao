"""Cross-process, crash-released locks on the same persistent worker volume."""
from contextlib import contextmanager
import hashlib
import os
from pathlib import Path


@contextmanager
def thread_lock(database_path, thread_id):
    directory = Path(str(database_path) + ".locks")
    directory.mkdir(parents=True, exist_ok=True)
    filename = hashlib.sha256(str(thread_id).encode()).hexdigest() + ".lock"
    with (directory / filename).open("a+b") as handle:
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        locked = False
        try:
            if os.name == "nt":
                import msvcrt
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    locked = True
                except OSError:
                    pass
            else:
                import fcntl
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    locked = True
                except BlockingIOError:
                    pass
            yield locked
        finally:
            if locked:
                handle.seek(0)
                if os.name == "nt":
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle, fcntl.LOCK_UN)
