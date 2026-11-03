"""Optional executor lease inheritance; absent environment preserves desktop calls."""
import os
from pathlib import Path
import stat


def inherited_lease_kwargs(env=None):
    if env is not None and os.environ.get('XL_PORTRAIT_RESOURCE_LOCK_FD'):
        for key in ('XL_PORTRAIT_RESOURCE_LOCK_FD', 'XL_PORTRAIT_RESOURCE_LOCK_PATH'):
            if env.get(key) != os.environ.get(key):
                raise ValueError('protected subprocess cannot drop or replace worker lease')
    env = os.environ if env is None else env
    raw = env.get('XL_PORTRAIT_RESOURCE_LOCK_FD')
    path = env.get('XL_PORTRAIT_RESOURCE_LOCK_PATH')
    if raw is None and path is None:
        return {}
    if os.name != 'posix' or not raw or not raw.isdecimal() or not path:
        raise ValueError('invalid protected worker lease environment')
    fd = int(raw)
    try:
        target = Path(path)
        if not target.is_absolute() or target.resolve(strict=True) != target or target.is_symlink():
            raise ValueError('unsafe protected worker lease path')
        actual = os.fstat(fd)
        expected = target.stat()
        if (fd < 3 or not stat.S_ISREG(actual.st_mode) or actual.st_uid != os.getuid()
                or actual.st_nlink != 1 or actual.st_mode & 0o077
                or (actual.st_dev, actual.st_ino) != (expected.st_dev, expected.st_ino)):
            raise ValueError('inherited lease descriptor mismatch')
        # Verify there is a live exclusive flock, without changing/unlocking it.
        import fcntl
        probe = os.open(target, os.O_RDWR | os.O_NOFOLLOW)
        try:
            try:
                fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                pass
            else:
                raise ValueError('inherited lease is not locked')
        finally:
            os.close(probe)
        # Same inode alone is insufficient: this open-file description must own
        # the flock, not a different descriptor held by the dispatcher.
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError('descriptor does not own inherited lease') from exc
    except OSError as exc:
        raise ValueError('protected worker lease unavailable') from exc
    return {'pass_fds': (fd,)}
