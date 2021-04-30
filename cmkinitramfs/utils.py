"""Library providing miscellaneous utilities used by cmkinitramfs"""

from __future__ import annotations

import functools
import hashlib
import os

from typing import no_type_check


# Function needed for python < 3.9
@no_type_check
def removeprefix(string: str, prefix: str) -> str:
    """Remove a prefix from a string

    Add support for :meth:`str.removeprefix` for Python < 3.9.

    :param string: String to remove prefix from
    :param prefix: Prefix to remove
    """
    if hasattr(str, 'removeprefix'):
        return string.removeprefix(prefix)
    elif string.startswith(prefix):
        return string[len(prefix):]
    return string


def normpath(path: str) -> str:
    """Normalize path (actually eliminates double slashes)

    :param path: Path to normalize
    """
    return os.path.normpath(path).replace('//', '/')


@functools.lru_cache()
def hash_file(filepath: str, chunk_size: int = 65536) -> bytes:
    """Calculate the SHA512 of a file

    :param filepath: Path of the file to hash
    :param chunk_size: Number of bytes per chunk of file to hash
    :return: File hash in a :class:`bytes` object
    """
    sha512 = hashlib.sha512()
    with open(filepath, 'rb') as src:
        for chunk in iter(lambda: src.read(chunk_size), b''):
            sha512.update(chunk)
    return sha512.digest()
