"""Library providing functions to build an initramfs

This library provides a main class :class:`Initramfs` which handles the content
of an initramfs. This class supports creating an initramfs tree inside
a directlry. It also can generate a CPIO file list compatible with the Linux
kernel's ``gen_init_cpio`` utility. (See
https://www.kernel.org/doc/html/latest/filesystems/ramfs-rootfs-initramfs.html
for mor details.)

Each file type of the initramfs has an :class:`Item` subclass. Those classes
provide methods used by :class:`Initramfs` generation methods.

This library also provides utilities like :func:`findexec`.
Multiple helper functions are also available (e.g. :func:`find_elf_deps_iter`
and :func:`find_elf_deps_set` to list libraries needed by an ELF executable).

The main function is :func:`mkinitramfs` to build the complete initramfs.
"""

from __future__ import annotations

import functools
import glob
import hashlib
import itertools
import logging
import os
import shutil
import socket
import stat
import subprocess
import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import (FrozenSet, IO, Iterable, Iterator, List, Optional, Set,
                    Tuple)

from elftools.common.exceptions import ELFError
from elftools.elf.elffile import ELFFile
from elftools.elf.enums import ENUM_DT_FLAGS_1

logger = logging.getLogger(__name__)
BINARY_KEYMAP_MAGIC = b'bkeymap'


def parse_ld_path(ld_path: Optional[str] = None, origin: str = '') \
        -> Iterator[str]:
    """Parse a colon-delimited list of paths and apply ldso rules

    Note the special handling as dictated by the ldso:
     - Empty paths are equivalent to $PWD
     - $ORIGIN is expanded to the path of the given file, ``origin``

    :param ld_path: Colon-delimited string of paths, defaults to the
        ``LD_LIBRARY_PATH`` environment variable
    :param origin: Directory containing the ELF file being parsed
        (used for $ORIGIN), defaults to an empty string
    :return: Iterator over the processed paths
    """

    if ld_path is None:
        ld_path = os.environ.get('LD_LIBRARY_PATH')
        if ld_path is None:
            return
    logger.debug("Parsing ld_path %s", ld_path)

    for path in ld_path.split(':'):
        if not path:
            yield os.getcwd()
        else:
            for k in (('$ORIGIN', origin), ('${ORIGIN}', origin)):
                path = path.replace(*k)
            yield path


def parse_ld_so_conf_iter(conf_path: str = '/etc/ld.so.conf') -> Iterator[str]:
    """Parse a ldso config file

    This should handle comments, whitespace, and "include" statements.

    :param conf_path: Path of the ldso config file to parse
    :return: Iterator over the processed paths
    """
    logger.debug("Parsing ld.so.conf %s", conf_path)

    with open(conf_path, 'r') as conf_file:
        for line in conf_file:
            line = line.split('#', 1)[0].strip()
            if not line:
                continue
            if line.startswith('include '):
                line = line[8:]
                if line[0] != '/':
                    line = os.path.dirname(conf_path) + '/' + line
                for path in sorted(glob.glob(line)):
                    yield from parse_ld_so_conf_iter(path)
            else:
                yield line


@functools.lru_cache()
def parse_ld_so_conf_tuple(conf_path: str = '/etc/ld.so.conf') \
        -> Tuple[str, ...]:
    """Parse a ldso config file

    Cached version of :func:`parse_ld_so_conf_iter`, returning a tuple.

    :param conf_path: Path of the ldso config file to parse
    :return: Tuple with the processed paths
    """
    return tuple(parse_ld_so_conf_iter(conf_path))


@functools.lru_cache()
def _get_default_libdirs() -> Tuple[str, ...]:
    """Get the default library directories"""
    libdirs = []
    for lib in ('lib64', 'lib', 'lib32'):
        for prefix in ('/', '/usr/'):
            if os.path.exists(prefix + lib):
                libdirs.append(prefix + lib)
    return tuple(libdirs)


@functools.lru_cache()
def _get_libdir(arch: int) -> str:
    """Get the libdir corresponding to a binary class

    :param arch: Binary class (e.g. :data:`32` or :data:`64`)
    """
    if arch == 64 and os.path.exists('/lib64'):
        return '/lib64'
    if arch == 32 and os.path.exists('/lib32'):
        return '/lib32'
    return '/lib'


def _is_elf_compatible(elf1: ELFFile, elf2: ELFFile) -> bool:
    """See if two ELFs are compatible

    This compares the aspects of the ELF to see if they're compatible:
    bit size, endianness, machine type, and operating system.

    :param elf1: First ELF object
    :param elf2: Second ELF object
    :return: :data:`True` if compatible, :data:`False` otherwise
    """

    osabis = frozenset([e.header['e_ident']['EI_OSABI'] for e in (elf1, elf2)])
    compat_sets = (frozenset(
        'ELFOSABI_%s' % x for x in ('NONE', 'SYSV', 'GNU', 'LINUX',)
    ),)
    return (
        (len(osabis) == 1 or any(osabis.issubset(x) for x in compat_sets)) and
        elf1.elfclass == elf2.elfclass and
        elf1.little_endian == elf2.little_endian and
        elf1.header['e_machine'] == elf2.header['e_machine']
    )


def _find_elf_deps_iter(elf: ELFFile, origin: str) \
        -> Iterator[Tuple[str, str]]:
    """Iterates over the dependencies of an ELF file

    Backend of :func:`find_elf_deps_iter`.

    :param elf: Elf file to parse
    :param origin: Directory containing the ELF binary (real path as provided
        by :func:`os.path.realpath`, used for $ORIGIN)
    :return: Same as :func:`find_elf_deps_iter`
    :raises FileNotFoundError: Dependency not found
    """
    deps: List[str] = []
    rpaths: List[str] = []
    runpaths: List[str] = []
    nodeflib = False

    # Read ELF segments
    for segment in elf.iter_segments():
        if segment.header.p_type == 'PT_INTERP':
            interp = segment.get_interp_name()
            logger.debug("INTERP: %s", interp)
            deps.append(interp)
        elif segment.header.p_type == 'PT_DYNAMIC':
            for tag in segment.iter_tags():
                if tag.entry.d_tag == 'DT_RPATH':
                    rpaths.extend(parse_ld_path(tag.rpath, origin))
                elif tag.entry.d_tag == 'DT_RUNPATH':
                    runpaths.extend(parse_ld_path(tag.runpath, origin))
                elif tag.entry.d_tag == 'DT_NEEDED':
                    deps.append(tag.needed)
                elif tag.entry.d_tag == 'DT_FLAGS_1':
                    if tag.entry.d_val & ENUM_DT_FLAGS_1['DF_1_NODEFLIB']:
                        nodeflib = True

    logger.debug("ELF: deps: %s, rpaths: %s, runpaths: %s, nodeflib: %s",
                 deps, rpaths, runpaths, nodeflib)

    # Directories in which dependencies will be searched
    search_paths = tuple(itertools.chain(
        rpaths, parse_ld_path(None, origin), runpaths,
        parse_ld_so_conf_tuple(), _get_default_libdirs()
    ))

    for dep in deps:

        # No need to search an absolute dependency
        if os.path.isabs(dep):
            logger.debug("Dependency is absolute: %s", dep)
            yield dep, dep
            continue

        for found_dir in search_paths:
            found_path = os.path.join(found_dir, dep)

            # Check found_path is valid and compatible
            if not os.path.exists(found_path):
                continue
            if nodeflib and found_dir in _get_default_libdirs():
                continue
            with open(found_path, 'rb') as found_file:
                try:
                    found_elf = ELFFile(found_file)
                except ELFError:
                    continue
                if not _is_elf_compatible(elf, found_elf):
                    continue
                found_arch = found_elf.elfclass

            # Lib found
            logger.debug("Found %s in %s", dep, found_dir)
            if found_dir in itertools.chain(rpaths, runpaths):
                # Libdir in R*PATH: use the same path
                yield found_path, found_path
            else:
                # Libdir in ld_path or ld.so.conf: use default libdir
                yield found_path, os.path.join(_get_libdir(found_arch), dep)
            break
        else:
            raise FileNotFoundError(f"ELF dependency not found: {dep}")


def find_elf_deps_iter(src: str) -> Iterator[Tuple[str, str]]:
    """Iterates over the dependencies of an ELF file

    Read an ELF file to search dynamic library dependencies. For each
    dependency, find it on the system (using RPATH, LD_LIBRARY_PATH,
    RUNPATH, ld.so.conf, and default library directories).

    If the library is in a path encoded in the ELF binary (RPATH or RUNPATH),
    ``dep_dest = dep_src``, otherwise use a default library directory
    according to the type of binary (``/lib``, ``/lib64``, ``/lib32``).

    If the file is not an ELF file, returns an empty iterator.

    :param src: File to find dependencies for
    :return: Iterator of ``(dep_src, dep_dest)``, with ``dep_src`` the path
        of the dependency on the current system, and ``dep_dest`` the
        path of the dependency on the initramfs
    :raises FileNotFoundError: Dependency not found
    """
    logger.debug("Searching ELF dependencies for %s", src)

    if src != os.path.realpath(src):
        yield from find_elf_deps_iter(os.path.realpath(src))
        return

    with open(src, 'rb') as src_file:
        try:
            elf = ELFFile(src_file)
        except ELFError:
            return
        yield from _find_elf_deps_iter(elf, os.path.dirname(src))
    logger.debug("Found all ELF dependencies for %s", src)


@functools.lru_cache()
def find_elf_deps_set(src: str) -> FrozenSet[Tuple[str, str]]:
    """Find dependencies of an ELF file

    Cached version of :func:`find_elf_deps_iter`.

    :param src: File to find dependencies for
    :return: Set of ``(dep_src, dep_dest)``, see :func:`find_elf_deps_iter`.
    :raises FileNotFoundError: Dependency not found
    """
    if src != os.path.realpath(src):
        return find_elf_deps_set(os.path.realpath(src))
    return frozenset(find_elf_deps_iter(src))


def findlib(lib: str, compat: str = sys.executable) -> Tuple[str, str]:
    """Search a library in the system

    Uses ``ld.so.conf`` and ``LD_LIBRARY_PATH``.

    Libraries will be installed in the default library directory in the
    initramfs.

    :param lib: Library to search (e.g. ``libgcc_s.so.1``)
    :param compat: Path to a binary that the library must be compatible with
        (checked with :func:`_is_elf_compatible`),
        defaults to :data:`sys.executable`
    :return: ``(lib_src, lib_dest)``, with ``lib_src`` the absolute path
        of the library on the current system, and ``lib_dest`` the absolute
        path of the library on the initramfs
    :raises FileNotFoundError: Library not found
    """
    logger.debug("Searching library %s", lib)
    libname = os.path.basename(lib)
    with open(compat, 'rb') as pyexec:
        pyelf = ELFFile(pyexec)

        if os.path.isfile(lib):
            return lib, os.path.join(_get_libdir(pyelf.elfclass), libname)

        search_paths = itertools.chain(
            parse_ld_path(), parse_ld_so_conf_tuple(),
            _get_default_libdirs()
        )
        for found_dir in search_paths:
            found_path = os.path.join(found_dir, libname)

            if not os.path.exists(found_path):
                continue
            with open(found_path, 'rb') as found_stream:
                try:
                    found_elf = ELFFile(found_stream)
                except ELFError:
                    continue
                if not _is_elf_compatible(pyelf, found_elf):
                    continue
                found_arch = found_elf.elfclass
            return found_path, os.path.join(_get_libdir(found_arch), libname)

    raise FileNotFoundError(lib)


def findexec(executable: str) -> str:
    """Search an executable in the system

    Uses the ``PATH`` environment variable.

    :param executable: Executable to search
    :return: Absolute path of the executable
    :raises FileNotFoundError: Executable not found
    """
    logger.debug("Searching executable %s", executable)

    if os.path.isfile(executable):
        return executable

    # Get set of directories to search
    execdirs = set()
    if os.environ.get('PATH') is not None:
        for k in os.environ['PATH'].split(':'):
            if k:
                execdirs.add(k)

    # Parse directories
    for execdir in execdirs:
        if os.path.isfile(f"{execdir}/{executable}"):
            return f"{execdir}/{executable}"
    raise FileNotFoundError(executable)


def busybox_get_applets(busybox_exec: str) -> Iterator[str]:
    """Get BusyBox applets

    :param busybox_exec: BusyBox executable (e.g. ``busybox``)
    :return: Iterator of absolute paths of BusyBox applets
    :raises subprocess.CalledProcessError: Error during ``busybox_exec``
    """
    cmd = [busybox_exec, '--list-full']
    with subprocess.Popen(cmd, stdout=subprocess.PIPE) as proc:
        assert proc.stdout is not None
        for line in proc.stdout:
            yield '/' + line.decode().strip()
        if proc.wait() != 0:
            raise subprocess.CalledProcessError(proc.returncode, proc.args)


def mkcpio_from_dir(src: str, dest: IO[bytes]) -> None:
    """Create CPIO archive from a given directory

    :param src: Directory from which the archive is created
    :param dest: Destination stream of the CPIO data
    :raises subprocess.CalledProcessError: Error during ``find`` or ``cpio``
    """
    logger.debug("Creating CPIO archive")

    oldpwd = os.getcwd()
    os.chdir(src)
    cmd = ["find", ".", "-print0"]
    with subprocess.Popen(cmd, stdout=subprocess.PIPE) as find:
        cmd = ['cpio', '--quiet', '--null', '--create', '--format=newc']
        with subprocess.Popen(cmd, stdin=find.stdout, stdout=dest) as cpio:
            if cpio.wait() != 0:
                raise subprocess.CalledProcessError(cpio.returncode, cpio.args)
        if find.wait() != 0:
            raise subprocess.CalledProcessError(find.returncode, find.args)
    os.chdir(oldpwd)


def mkcpio_from_list(src: str, dest: IO[bytes]) -> None:
    """Create CPIO archive from a given CPIO list

    :param src: Path of the CPIO list
    :param dest: Destination stream of the CPIO data
    :raises subprocess.CalledProcessError: Error during ``gen_init_cpio``
    """
    subprocess.check_call(['gen_init_cpio', src], stdout=dest)


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


class MergeError(Exception):
    """Cannot merge an Item into another"""


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
        else:
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
    """
    mode: int
    user: int
    group: int
    dests: Set[str]
    src: str
    data_hash: bytes

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
        shutil.copy(self.src, base_dest, follow_symlinks=True)
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


class Initramfs:
    """An initramfs archive

    :param user: Default user to use when creating items
    :param group: Default group to use when creating items
    :param items: Items in the initramfs
    """
    user: int
    group: int
    items: List[Item]

    def __init__(self, user: int = 0, group: int = 0) -> None:
        self.user = user
        self.group = group
        self.items = []
        self.__mklayout()

    def __mklayout(self) -> None:
        """Create the base layout of the initramfs"""
        logger.debug("Creating initramfs layout")

        self.items.append(Directory(0o755, self.user, self.group, '/'))

        # Base layout
        self.add_item(Directory(0o755, self.user, self.group, '/bin'))
        self.add_item(Directory(0o755, self.user, self.group, '/dev'))
        self.add_item(Directory(0o755, self.user, self.group, '/etc'))
        self.add_item(Directory(0o755, self.user, self.group, '/mnt'))
        self.add_item(Directory(0o755, self.user, self.group, '/proc'))
        self.add_item(Directory(0o755, self.user, self.group, '/root'))
        self.add_item(Directory(0o755, self.user, self.group, '/run'))
        self.add_item(Directory(0o755, self.user, self.group, '/sbin'))
        self.add_item(Directory(0o755, self.user, self.group, '/sys'))

        # Only create /lib* if they exists on the current system
        for libdir in ["/lib", "/lib32", "/lib64"]:
            if os.path.islink(libdir):
                self.add_item(Symlink(0o777, self.user, self.group,
                                      libdir, os.readlink(libdir)))
            elif os.path.isdir(libdir):
                self.add_item(Directory(0o755, self.user, self.group, libdir))

        # Create necessary character devices
        self.add_item(Node(0o600, self.user, self.group, '/dev/console',
                           Node.NodeType.CHARACTER, 5, 1))
        self.add_item(Node(0o666, self.user, self.group, '/dev/tty',
                           Node.NodeType.CHARACTER, 5, 0))
        self.add_item(Node(0o666, self.user, self.group, '/dev/null',
                           Node.NodeType.CHARACTER, 1, 3))

    def __iter__(self) -> Iterator[Item]:
        """Iterate over the :class:`Item` instances in the initramfs"""
        return iter(self.items)

    def __contains__(self, path: str) -> bool:
        """Check if a path exists on the initramfs

        :param path: Path to check
        :return: :data:`True` if ``path`` exists on the initramfs,
            :data:`False` otherwise
        """
        for item in self:
            if path in item:
                return True
        return False

    def add_item(self, new_item: Item) -> None:
        """Add an item to the initramfs

        If an identical item is already present, merges them together.

        :param new_item: :class:`Item` instance to add
        :raises MergeError: Item cannot be merged into the initramfs
            (missing parent directory or file conflict)
        """

        #: Used to check all parents directories exist and are created
        #: before the creation/merge of new_item
        parents = {os.path.dirname(k): False for k in new_item if k != '/'}
        mergeable = None
        for cur_item in self:
            # Check if new_item can be merged or is conflicting with cur_item
            if cur_item.is_mergeable(new_item):
                assert mergeable is None
                mergeable = cur_item
            for dest in new_item:
                if cur_item is not mergeable and dest in cur_item:
                    raise MergeError(
                        f"File collision between {new_item} and {cur_item}"
                    )
                if mergeable is None and os.path.dirname(dest) in cur_item:
                    parents[os.path.dirname(dest)] = True

        if not all(parents.values()):
            missings = tuple(k for k in parents if not parents[k])
            raise MergeError(f"Missing directory: {missings}")
        if mergeable is not None:
            mergeable.merge(new_item)
        else:
            # Add new_item
            self.items.append(new_item)
            logger.debug("New item: %s", new_item)

    @staticmethod
    def __normalize(path: str) -> str:
        """Normalize a path for the initramfs filesystem

        Strip /usr[/local] directory, warns if spaces are present in
        the path.

        :param path: Destination path to normalize
        :return: Normalized path
        :raises ValueError: Invalid path
        """

        path = os.path.normpath(path)
        # Initramfs path must be absolute
        if not os.path.isabs(path):
            raise ValueError(f"{path} is not an absolute path")
        # Strip /usr directory, not needed in initramfs
        if "/usr/local/" in path:
            logger.debug("Stripping /usr/local/ from %s", path)
            path = path.replace("/usr/local", "/")
        elif "/usr/" in path:
            logger.debug("Stripping /usr/ from %s", path)
            path = path.replace("/usr/", "/")
        # Check whitespaces
        if ' ' in path or '\t' in path or '\n' in path:
            logger.warning("Whitespaces are not supported by gen_init_cpio: "
                           "%s", path)
        return path

    @functools.lru_cache()
    def add_file(self, src: str, dest: Optional[str] = None,
                 mode: Optional[int] = None) -> None:
        """Add a file to the initramfs

        If the file is a symlink, it is dereferenced.
        If it is a dynamically linked ELF file, its dependencies
        are also added.

        :param src: Absolute or relative path of the source file
        :param dest: Absolute path of the destination, relative to the
            initramfs root, defaults to ``src``
        :param mode: File permissions to use, defaults to same as ``src``
        :raises FileNotFoundError: Source file or ELF dependency not found
        :raises MergeError: Destination file exists and is different,
            or missing parent directory (raised from :meth:`add_item`)
        """

        # Sanity checks
        if not os.path.exists(src):
            raise FileNotFoundError(src)

        # Configure paths
        src = os.path.abspath(src)
        if not dest:
            dest = src
        dest = Initramfs.__normalize(dest)

        logger.debug("Adding %s as %s", src, dest)

        # Copy dependencies
        for dep_src, dep_dest in find_elf_deps_set(src):
            self.add_file(dep_src, dep_dest)

        # Add file
        if mode is None:
            mode = os.stat(src, follow_symlinks=True).st_mode & 0o7777
        self.add_item(File(mode, self.user, self.group,
                           {dest}, src, hash_file(src)))

    def build_to_cpio_list(self, dest: IO[str]) -> None:
        """Write a CPIO list into a file

        This list is compatible with Linux's ``gen_init_cpio``.
        See :meth:`Item.build_to_cpio_list`.

        :param dest: Stream in which the list is written
        """
        for item in self:
            logger.debug("Outputting %s", item)
            dest.write(item.build_to_cpio_list())
            dest.write('\n')

    def build_to_directory(self, dest: str, do_nodes: bool = True) -> None:
        """Copy or create all items to a real filesystem

        See :meth:`Item.build_to_directory`.

        :param dest: Path to use as root directory of the initramfs
        :param do_nodes: Also creates :class:`Node` items, (used for debugging:
            ``CAP_MKNOD`` is needed to create some special devices)
        """
        for item in self:
            if not do_nodes and isinstance(item, Node):
                logger.warning("Not building Node %s", item)
                continue
            logger.debug("Building %s", item)
            item.build_to_directory(dest)


def mkinitramfs(
        initramfs: Initramfs,
        init: str,
        files: Optional[Iterable[Tuple[str, Optional[str]]]] = None,
        execs: Optional[Iterable[Tuple[str, Optional[str]]]] = None,
        libs: Optional[Iterable[Tuple[str, Optional[str]]]] = None,
        keymap: Optional[Tuple[str, str]] = None,
        ) -> None:
    """Add given files to the initramfs

    :param initramfs: :class:`Initramfs` instance to which the files will be
        added.
    :param init: Path of the init script to use (the script can be generated
        with :func:`cmkinitramfs.mkinit.mkinit`).
    :param files: Files to add to the initramfs, each tuple is in the format
        ``(src, dest)``. ``src`` is the path on the current system, ``dest``
        is the path within the initramfs. This is the same format as
        described in :meth:`cmkinitramfs.mkinit.Data.deps_files`.
    :param execs: Executables to add to the initramfs. ``src`` can be the
        base name, it will be searched on the system with :func:`findexec`.
        Same format as :meth:`cmkinitramfs.mkinit.Data.deps_files`.
    :param libs: Libraries to add to the initramfs. ``src`` can be the
        base name, it will be searched on the system with :func:`findlib`.
        Same format as :meth:`cmkinitramfs.mkinit.Data.deps_files`.
    :param keymap: Tuple in the format ``(src, dest)``. ``src`` is the
        keymap to add to the initramfs, ``dest`` is the path of the keymap
        within the initramfs. If this argument is :data:`None`, no keymap
        will be added to the initramfs.
    """

    if files is None:
        files = set()
    if execs is None:
        execs = set()
    if libs is None:
        libs = set()

    # Add necessary files
    for fsrc, fdest in files:
        logger.info("Adding file %s", fsrc)
        initramfs.add_file(fsrc, fdest)
    for fsrc, fdest in execs:
        logger.info("Adding executable %s", fsrc)
        initramfs.add_file(findexec(fsrc), fdest)
    for fsrc, fdest in libs:
        logger.info("Adding library %s", fsrc)
        lib_src, lib_dest = findlib(fsrc)
        initramfs.add_file(lib_src, fdest if fdest is not None else lib_dest)

    # Add keymap
    if keymap is not None:
        logger.info("Adding keymap as %s", keymap[1])
        initramfs.add_file(*keymap, mode=0o644)
        with open(keymap[0], 'rb') as bkeymap:
            if bkeymap.read(len(BINARY_KEYMAP_MAGIC)) != BINARY_KEYMAP_MAGIC:
                logger.error("Binary keymap %s: bad file format", keymap[0])

    # Add /init
    logger.info("Adding init script")
    initramfs.add_file(init, "/init", mode=0o755)

    # Add busybox
    logger.info("Adding busybox")
    busybox = findexec('busybox')
    initramfs.add_file(busybox)
    for applet in busybox_get_applets(busybox):
        try:
            initramfs.add_file(busybox, applet)
        except MergeError:
            logging.debug("Not adding applet %s: file exists", applet)


def keymap_build(src: str, dest: IO[bytes]) -> None:
    """Generate a binary keymap from a keymap name

    This keymap can then be loaded with ``loadkmap`` from the initramfs
    environment.

    :param src: Name of the keymap to convert, can be a keyboard layout name
        or a file path
    :param dest: Destination stream to write into
    :raises subprocess.CalledProcessError: Error during ``loadkeys``
    """
    subprocess.check_call(['loadkeys', '--bkeymap', src], stdout=dest)
