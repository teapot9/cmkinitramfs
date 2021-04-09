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
Multiple helper functions are also available (e.g. :func:`find_elf_deps` to
list libraries needed by an ELF executable).

The main function is :func:`mkinitramfs` to build the complete initramfs.
"""

import glob
import hashlib
import logging
import os
import shutil
import socket
import stat
import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import IO, Iterable, Iterator, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)
BINARY_KEYMAP_MAGIC = b'bkeymap'


def findlib(lib: str) -> str:
    """Search a library in the system

    Uses ``/etc/ld.so.conf``, ``/etc/ld.so.conf.d/*.conf``
    and the ``LD_LIBRARY_PATH`` environment variable.

    :param lib: Library to search
    :return: Absolute path of the library
    :raises FileNotFoundError: Library not found
    """
    logger.debug("Searching library %s", lib)

    if os.path.isfile(lib):
        return lib

    # Get list of directories to search
    libdirs = []
    if os.environ.get('LD_LIBRARY_PATH') is not None:
        for k in os.environ['LD_LIBRARY_PATH'].split(':'):
            if k:
                libdirs.append(k)

    # List files in /etc/ld.so.conf and /etc/ld.so.conf.d/*.conf
    dirlists = glob.glob("/etc/ld.so.conf") \
        + glob.glob("/etc/ld.so.conf.d/*.conf")

    # For each file, add listed directories to libdirs
    for dirlist in dirlists:
        with open(dirlist, "r", encoding="utf8") as file_dirlist:
            for line in file_dirlist:
                if os.path.exists(line.strip()):
                    libdirs.append(line.strip())

    # Parse directories
    for libdir in libdirs:
        if os.path.isfile(f"{libdir}/{lib}"):
            return f"{libdir}/{lib}"
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


def find_elf_deps(src: str) -> Iterator[str]:
    """Find dependencies of an ELF file

    If the file is not an ELF file or has no dependency,
    nothing is yielded. This will not cause any error.

    :param src: File to find dependencies for
    :return: Iterator of absolute paths of the dependencies
    :raises subprocess.CalledProcessError: Error during ``lddtree``
    """
    logger.debug("Parsing ELF deps for %s", src)

    src = os.path.abspath(src)
    cmd = ["lddtree", "--list", "--skip-non-elfs", src]
    with subprocess.Popen(cmd, stdout=subprocess.PIPE) as proc:
        assert proc.stdout is not None
        for line in proc.stdout:
            fname = os.path.abspath(line.decode().strip())
            if fname != src:
                yield fname
        if proc.wait() != 0:
            raise subprocess.CalledProcessError(proc.returncode, proc.args)


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

    def merge(self, other: 'Item') -> None:
        """Merge two items together

        By default, two items can only be merged if they are equal.
        Default merge is just a no-op. Subclasses can override this
        as done by :class:`File` to handle hardlink of identical files.

        :param other: :class:`Item` to merge into ``self``
        :raises MergeError: Cannot merge the items
        """
        if self != other:
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

    def merge(self, other: Item) -> None:
        if isinstance(other, File) \
                and self.data_hash == other.data_hash \
                and self.mode == other.mode \
                and self.user == other.user \
                and self.group == other.group:
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
            + f'{self.mode:03o} {self.user} {self.mode}' \
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
    nodetype: 'Node.NodeType'
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

    def add_item(self, new_item: Item) -> None:
        """Add an item to the initramfs

        If an identical item is already present, merges them together.

        :param new_item: :class:`Item` instance to add
        :raises MergeError: Item cannot be merged into the initramfs
            (missing parent directory or file conflict)
        """

        for cur_item in self:
            # Check if the item can be merged with any existing item
            try:
                cur_item.merge(new_item)
                break
            except MergeError as error:
                for dest in cur_item:
                    if dest in new_item:
                        raise MergeError(
                            f"File collision bewteen {new_item} and {cur_item}"
                        ) from error

        else:
            # Check if the parent directory of all destinations exists
            for dest in new_item:
                dirname = os.path.dirname(dest)
                for item in self:
                    if isinstance(item, Directory) \
                            and dirname in item:
                        break
                else:
                    raise MergeError(f"Missing directory: {dirname}")
            self.items.append(new_item)

    def add_file(self, src: str, dest: Optional[str] = None,
                 deps: bool = True, mode: Optional[int] = None) -> None:
        """Add a file to the initramfs

        If the file is a symlink, it is dereferenced.
        If it is a dynamically linked ELF file, its dependencies
        are also added (if ``deps`` is :data:`True`).

        :param src: Absolute or relative path of the source file
        :param dest: Absolute path of the destination, relative to the
            initramfs root, defaults to ``src``
        :param deps: Add needed dependencies (ELF)
        :param mode: File permissions to use, defaults to same as ``src``
        :raises FileNotFoundError: Source file not found
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
        # Strip /usr directory, not needed in initramfs
        if "/usr/local/" in dest:
            logger.debug("Stripping /usr/local/ from %s", dest)
            dest = dest.replace("/usr/local", "/")
        elif "/usr/" in dest:
            logger.debug("Stripping /usr/ from %s", dest)
            dest = dest.replace("/usr/", "/")
        # Check whitespaces
        if ' ' in dest or '\t' in dest or '\n' in dest:
            logger.warning("Whitespaces are not supported by gen_init_cpio: "
                           "%s", dest)

        # Copy dependencies
        if deps:
            for dep in find_elf_deps(src):
                self.add_file(dep, deps=False)

        logger.debug("Adding %s as %s", src, dest)
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
    logger.info("Adding files %s", [k[0] for k in files])
    for fsrc, fdest in files:
        initramfs.add_file(fsrc, fdest)
    logger.info("Adding executables %s", [k[0] for k in execs])
    for fsrc, fdest in execs:
        initramfs.add_file(findexec(fsrc), fdest)
    logger.info("Adding libraries %s", [k[0] for k in libs])
    for fsrc, fdest in libs:
        initramfs.add_file(findlib(fsrc), fdest)

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
            initramfs.add_file(busybox, applet, deps=False)
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
