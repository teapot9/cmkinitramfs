"""Module providing the Item class for files in the initramfs

Each file type of the initramfs has an :class:`Item` subclass. Those classes
provide methods used by :class:`Initramfs` generation methods.
"""

from __future__ import annotations

import logging
import os
import socket
import stat
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Iterator, Set

from .utils import hash_file


logger = logging.getLogger(__name__)


class MergeError(Exception):
    """Cannot merge an Item into another"""


@dataclass  # type: ignore
class Item(ABC):
    """An object within the initramfs"""

    def is_mergeable(self, other: Item) -> bool:
        """Check if two items can be merged together

        By default, two items can only be merged if they are equal.

        :param other: :class:`Item` to merge into ``self``
        :return: :data:`True` if the items can be merged,
            :data:`False` otherwise
        """
        return self == other

    def merge(self, other: Item) -> None:
        """Merge two items together

        Default merge is just a no-op. Subclasses can override this
        as done by :class:`File` to handle hardlink of identical files.

        :param other: :class:`Item` to merge into ``self``
        :raises MergeError: Cannot merge the items
        """
        if not self.is_mergeable(other):
            raise MergeError(f"Different items: {self} != {other}")

    def __iter__(self) -> Iterator[str]:
        """Get the paths of this item within the initramfs

        This method should be overriden by subclasses which add files
        to the initramfs.

        :return: Iterator over this :class:`Item`'s destination paths
        """
        return iter(())

    def __contains__(self, path: str) -> bool:
        """Check if this item is present at the given path in the initramfs

        This method should be overriden by subclasses which add files
        to the initramfs.

        :return: :data:`True` if ``path`` is part of this :class:`Item`'s
            destination paths, :data:`False` otherwise
        """
        return False

    @staticmethod
    def build_from_cpio_list(data: str) -> Item:
        """Build an Item from a string

        This string should respect the format of ``gen_init_cpio``.

        :param data: String to parse
        :return: Item corresponding to ``data``
        :raises ValueError: Invalid string
        """
        parts = data.split()
        if parts[0] == 'file' and len(parts) >= 6:
            return File(
                int(parts[3], base=8), int(parts[4]), int(parts[5]),
                set(parts[6:] + [parts[1]]), parts[2], hash_file(parts[2])
            )
        if parts[0] == 'dir' and len(parts) == 5:
            return Directory(
                int(parts[2], base=8), int(parts[3]), int(parts[4]), parts[1]
            )
        if parts[0] == 'nod' and len(parts) == 8:
            return Node(
                int(parts[2], base=8), int(parts[3]), int(parts[4]), parts[1],
                Node.NodeType(parts[5]), int(parts[6]), int(parts[7])
            )
        if parts[0] == 'slink' and len(parts) == 6:
            return Symlink(
                int(parts[3], base=8), int(parts[4]), int(parts[5]),
                parts[1], parts[2]
            )
        if parts[0] == 'pipe' and len(parts) == 5:
            return Pipe(
                int(parts[2], base=8), int(parts[3]), int(parts[4]), parts[1]
            )
        if parts[0] == 'sock' and len(parts) == 5:
            return Socket(
                int(parts[2], base=8), int(parts[3]), int(parts[4]), parts[1]
            )
        if parts[0] in ('file', 'dir', 'nod', 'slink', 'pipe', 'sock'):
            raise ValueError(f"Invalid format for {parts[0]}: {parts[1:]}")
        raise ValueError(f"Unknown type: {parts[0]}")

    @abstractmethod
    def build_to_cpio_list(self) -> str:
        """String representing the item

        The string is formatted to be compatible with the ``gen_init_cpio``
        tool from the Linux kernel.
        This method has to be defined by subclasses.
        """

    @abstractmethod
    def build_to_directory(self, base_dir: str) -> None:
        """Add this item to a real filesystem

        This will copy or create a file on a real filesystem.
        This method has to be defined by subclasses.

        :param base_dir: Path to use as root directory (e.g. using
            ``/tmp/initramfs``, ``/bin/ls`` will be copied to
            ``/tmp/initramfs/bin/ls``)
        """


@dataclass
class File(Item):
    """Normal file within the initramfs

    :param mode: Permissions (e.g. 0o644)
    :param user: Owner user (UID)
    :param group: Owner group (GID)
    :param dests: Paths in the initramfs (hard-linked)
    :param src: Source file to copy (not unique to the file)
    :param data_hash: Hash of the file (can be obtained with :func:`hash_file`)
    :param chunk_size: Chunk size to use when copying the file
    """
    mode: int
    user: int
    group: int
    dests: Set[str]
    src: str
    data_hash: bytes
    chunk_size: int = 65536

    def __str__(self) -> str:
        return f"file from {self.src}"

    def is_mergeable(self, other: Item) -> bool:
        return isinstance(other, File) \
            and self.data_hash == other.data_hash \
            and self.mode == other.mode \
            and self.user == other.user \
            and self.group == other.group

    def merge(self, other: Item) -> None:
        if self.is_mergeable(other):
            assert isinstance(other, File)
            self.dests |= other.dests
        else:
            raise MergeError(f"Different files: {self} and {other}")

    def __iter__(self) -> Iterator[str]:
        return iter(self.dests)

    def __contains__(self, path: str) -> bool:
        return path in self.dests

    def build_to_cpio_list(self) -> str:
        dests = iter(sorted(self.dests))
        return f'file {next(dests)} {self.src} ' \
            + f'{self.mode:03o} {self.user} {self.group}' \
            + (' ' if len(self.dests) > 1 else '') \
            + ' '.join(dests)

    def build_to_directory(self, base_dir: str) -> None:
        iter_dests = iter(self.dests)
        # Copy reference file
        base_dest = base_dir + next(iter_dests)
        with open(self.src, 'rb') as src_file, \
                open(base_dest, 'wb') as dest_file:
            for chunk in iter(lambda: src_file.read(self.chunk_size), b''):
                dest_file.write(chunk)
        os.chmod(base_dest, self.mode)
        os.chown(base_dest, self.user, self.group)
        # Hardlink other files
        for dest in iter_dests:
            abs_dest = base_dir + dest
            os.link(base_dest, abs_dest)


@dataclass
class Directory(Item):
    """Directory within the initramfs

    :param mode: Permissions (e.g. 0o644)
    :param user: Owner user (UID)
    :param group: Owner group (GID)
    :param dest: Path in the initramfs
    """
    mode: int
    user: int
    group: int
    dest: str

    def __str__(self) -> str:
        return f"directory {self.dest}"

    def __iter__(self) -> Iterator[str]:
        return iter((self.dest,))

    def __contains__(self, path: str) -> bool:
        return path == self.dest

    def build_to_cpio_list(self) -> str:
        return f'dir {self.dest} {self.mode:03o} {self.user} {self.group}'

    def build_to_directory(self, base_dir: str) -> None:
        abs_dest = base_dir + self.dest
        os.mkdir(abs_dest)
        os.chmod(abs_dest, self.mode)
        os.chown(abs_dest, self.user, self.group)


@dataclass
class Node(Item):
    """Special file within the initramfs

    :param mode: Permissions (e.g. 0o644)
    :param user: Owner user (UID)
    :param group: Owner group (GID)
    :param dest: Path in the initramfs
    :param nodetype: Type of node (block, character)
    :param major: Major number of the node
    :param minor: Minor number of the node
    """
    mode: int
    user: int
    group: int
    dest: str
    nodetype: Node.NodeType
    major: int
    minor: int

    class NodeType(Enum):
        """Special file type"""
        #: Block device
        BLOCK = 'b'
        #: Character device
        CHARACTER = 'c'

    def __str__(self) -> str:
        if self.nodetype == Node.NodeType.CHARACTER:
            return f"character device {self.major} {self.minor} {self.dest}"
        if self.nodetype == Node.NodeType.BLOCK:
            return f"block device {self.major} {self.minor} {self.dest}"
        raise ValueError(f"Unknown node type {self.nodetype}")

    def __iter__(self) -> Iterator[str]:
        return iter((self.dest,))

    def __contains__(self, path: str) -> bool:
        return path == self.dest

    def build_to_cpio_list(self) -> str:
        return f'nod {self.dest} {self.mode:03o} {self.user} {self.group} ' \
            f'{self.nodetype.value} {self.major} {self.minor}'

    def build_to_directory(self, base_dir: str) -> None:
        abs_dest = base_dir + self.dest
        if self.nodetype == Node.NodeType.BLOCK:
            mode = self.mode | stat.S_IFBLK
        elif self.nodetype == Node.NodeType.CHARACTER:
            mode = self.mode | stat.S_IFCHR
        os.mknod(abs_dest, mode, os.makedev(self.major, self.minor))
        os.chmod(abs_dest, self.mode)
        os.chown(abs_dest, self.user, self.group)


@dataclass
class Symlink(Item):
    """Symlinks within the initramfs

    :param mode: Permissions (e.g. 0o644)
    :param user: Owner user (UID)
    :param group: Owner group (GID)
    :param dest: Path in the initramfs
    :param target: Link target
    """
    mode: int
    user: int
    group: int
    dest: str
    target: str

    def __str__(self) -> str:
        return f"symlink {self.dest} to {self.target}"

    def __iter__(self) -> Iterator[str]:
        return iter((self.dest,))

    def __contains__(self, path: str) -> bool:
        return path == self.dest

    def build_to_cpio_list(self) -> str:
        return f'slink {self.dest} {self.target} ' \
            f'{self.mode:03o} {self.user} {self.group}'

    def build_to_directory(self, base_dir: str) -> None:
        if self.mode != 0o777:
            logger.warning("Cannot set mode for %s", self)
        abs_dest = base_dir + self.dest
        os.symlink(self.target, abs_dest)
        os.chown(abs_dest, self.user, self.group, follow_symlinks=False)


@dataclass
class Pipe(Item):
    """Named pipe (FIFO) within the initramfs

    :param mode: Permissions (e.g. 0o644)
    :param user: Owner user (UID)
    :param group: Owner group (GID)
    :param dest: Path in the initramfs
    """
    mode: int
    user: int
    group: int
    dest: str

    def __str__(self) -> str:
        return f"named pipe {self.dest}"

    def __iter__(self) -> Iterator[str]:
        return iter((self.dest,))

    def __contains__(self, path: str) -> bool:
        return path == self.dest

    def build_to_cpio_list(self) -> str:
        return f'pipe {self.dest} {self.mode:03o} {self.user} {self.group}'

    def build_to_directory(self, base_dir: str) -> None:
        abs_dest = base_dir + self.dest
        os.mkfifo(abs_dest)
        os.chmod(abs_dest, self.mode)
        os.chown(abs_dest, self.user, self.group)


@dataclass
class Socket(Item):
    """Named socket within the initramfs

    :param mode: Permissions (e.g. 0o644)
    :param user: Owner user (UID)
    :param group: Owner group (GID)
    :param dest: Path in the initramfs
    """
    mode: int
    user: int
    group: int
    dest: str

    def __str__(self) -> str:
        return f"named socket {self.dest}"

    def __iter__(self) -> Iterator[str]:
        return iter((self.dest,))

    def __contains__(self, path: str) -> bool:
        return path == self.dest

    def build_to_cpio_list(self) -> str:
        return f'sock {self.dest} {self.mode:03o} {self.user} {self.group}'

    def build_to_directory(self, base_dir: str) -> None:
        abs_dest = base_dir + self.dest
        sock = socket.socket(socket.AF_UNIX)
        sock.bind(abs_dest)
        os.chmod(abs_dest, self.mode)
        os.chown(abs_dest, self.user, self.group)
