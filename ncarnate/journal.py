"""Reserve reporting space before conversion, then publish a complete journal."""
import os
import tempfile
import stat


def unlinked_path(path):
    """Resolve parent aliases (including macOS /tmp), refusing a linked leaf."""
    path = os.path.abspath(path)
    if os.path.lexists(path):
        status = os.lstat(path)
        if os.path.islink(path) or getattr(status, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0):
            raise OSError("final path component is a link or reparse point")
    return os.path.join(os.path.realpath(os.path.dirname(path)), os.path.basename(path))


class ResultJournal:
    def __init__(self, target):
        self.target = unlinked_path(target)
        if os.path.islink(self.target) or (os.path.exists(self.target)
                                         and not os.path.isfile(self.target)):
            raise OSError("journal target must be a regular file")
        if os.path.exists(self.target) and not os.access(self.target, os.W_OK):
            raise OSError("journal target is not writable")
        fd, self.temporary = tempfile.mkstemp(
            prefix=".ncarnate-journal-", dir=os.path.dirname(self.target),
        )
        self.stream = os.fdopen(fd, "w", encoding="utf-8", newline="")

    def publish(self, text):
        self.stream.write(text + "\n" if text else "")
        self.stream.flush()
        os.fsync(self.stream.fileno())
        self.stream.close()
        os.replace(self.temporary, self.target)

    def close(self):
        try:
            self.stream.close()
        finally:
            if os.path.exists(self.temporary):
                os.unlink(self.temporary)
