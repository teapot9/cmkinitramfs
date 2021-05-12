"""Module providing functions and classes to build an initramfs

This library provides a class :class:`Initramfs` which handles the content
of an initramfs. This class supports creating an initramfs tree inside
a directlry. It also can generate a CPIO file list compatible with the Linux
kernel's ``gen_init_cpio`` utility. (See
https://www.kernel.org/doc/html/latest/filesystems/ramfs-rootfs-initramfs.html
for mor details.)

The main function is :func:`mkinitramfs` to build the complete initramfs.
"""

from __future__ import annotations

import logging
import os
import os.path
import platform
import subprocess
from typing import IO, Iterable, Iterator, List, Optional, Set, Tuple

from .bin import (find_elf_deps_set, find_kmod, find_kmod_deps,
                  find_exec, find_lib)
from .item import Directory, File, Item, MergeError, Node, Symlink
from .utils import hash_file, normpath, removeprefix

logger = logging.getLogger(__name__)
#: Set of shell special built-in commands.
#: They are guaranteed to be available in the initramfs' ``/bin/sh``.
SHELL_SPECIAL_BUILTIN = frozenset((
    'break', ':', 'continue', '.', 'eval', 'exec', 'exit', 'export',
    'readonly', 'return', 'set', 'shift', 'times', 'trap', 'unset',
))
#: Set of shell reserved words.
#: They are guaranteed to be available in the initramfs' ``/bin/sh``.
SHELL_RESERVED_WORDS = frozenset((
    '!', '{', '}', 'case', 'do', 'done', 'elif', 'else', 'esac', 'fi', 'for',
    'if', 'in', 'then', 'until', 'while',
))


def busybox_get_applets(busybox_exec: str) -> Iterator[str]:
    """Get BusyBox applets

    :param busybox_exec: BusyBox executable (e.g. ``busybox``)
    :return: Iterator of absolute paths of BusyBox applets
    :raises subprocess.CalledProcessError: Error during ``busybox_exec``
    """
    cmd = (busybox_exec, '--list-full')
    logger.debug("Subprocess: %s", cmd)
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
    cmd: Tuple[str, ...] = ("find", ".", "-print0")
    logger.debug("Subprocess: %s", cmd)
    with subprocess.Popen(cmd, stdout=subprocess.PIPE) as find:
        cmd = ('cpio', '--quiet', '--null', '--create', '--format=newc')
        logger.debug("Subprocess: %s", cmd)
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
    cmd = ('gen_init_cpio', src)
    logger.debug("Subprocess: %s", cmd)
    subprocess.check_call(cmd, stdout=dest)


def keymap_build(src: str, dest: IO[bytes], unicode: bool = True) -> None:
    """Generate a binary keymap from a keymap name

    This keymap can then be loaded with ``loadkmap`` from the initramfs
    environment.

    :param src: Name of the keymap to convert, can be a keyboard layout name
        or a file path
    :param dest: Destination stream to write into
    :param unicode: Generate a unicode keymap (rather than ASCII)
    :raises subprocess.CalledProcessError: Error during ``loadkeys``
    """
    cmd = ('loadkeys', '--unicode' if unicode else '--ascii', '--bkeymap', src)
    logger.debug("Subprocess: %s", cmd)
    subprocess.check_call(cmd, stdout=dest)


class Initramfs:
    """An initramfs archive

    :param user: Default user to use when creating items
    :param group: Default group to use when creating items
    :param binroot: Root directory where binary files are found
        (executables and libraries)
    :param kernels: Kernel versions of the initramfs,
        defaults to the running kernel version
    :param items: Items in the initramfs
    """
    user: int
    group: int
    binroot: str
    kernels: Set[str]
    items: List[Item]

    def __init__(self, user: int = 0, group: int = 0, binroot: str = '/',
                 kernels: Optional[Iterable[str]] = None) -> None:
        self.user = user
        self.group = group
        self.binroot = binroot
        self.kernels = set(kernels) if kernels is not None \
            else {platform.release()}
        logger.debug("Target kernels: %s", self.kernels)
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
        self.add_item(Node(0o644, self.user, self.group, '/dev/kmsg',
                           Node.NodeType.CHARACTER, 1, 11))

        # Add kernel modules information
        for kernel in self.kernels:
            kmod_dir = f'/lib/modules/{kernel}'
            self.mkdir(kmod_dir, mode=0o755, parents=True)
            self.add_file(kmod_dir + '/modules.order', mode=0o640)
            self.add_file(kmod_dir + '/modules.builtin', mode=0o640)
            self.add_file(kmod_dir + '/modules.builtin.modinfo', mode=0o640)

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

    def __eq__(self, other: object) -> bool:
        return isinstance(other, type(self)) and self.user == other.user \
            and self.group == other.group and self.binroot == other.binroot \
            and self.kernels == other.kernels and self.items == other.items

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
            logger.error("Cannot add %s: missing directories %s",
                         new_item, missings)
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

        path = normpath(path)
        # Initramfs path must be absolute
        if not os.path.isabs(path):
            raise ValueError(f"{path} is not an absolute path")
        # Strip /usr directory, not needed in initramfs
        if path.startswith('/usr/local/'):
            logger.debug("Stripping /usr/local/ from %s", path)
            path = removeprefix(path, '/usr/local')
        elif path.startswith('/usr/'):
            logger.debug("Stripping /usr/ from %s", path)
            path = removeprefix(path, '/usr')
        # Check whitespaces
        if len(path.split()) != 1:
            logger.warning("Whitespaces are not supported by gen_init_cpio: "
                           "%s", path)
        return path

    def mkdir(self, path: str, mode: int = 0o755, parents: bool = False) \
            -> None:
        """Create a directory on the initramfs

        :param path: Absolute path of the directory,
            relative to the initramfs root
        :param mode: File permissions to use
        :param parents: If :data:`True`, missing parent directories will
            also be created
        :raises MergeError: Destination file exists and is different,
            or missing parent directory (raised from :meth:`add_item`)
        """
        logger.debug("Creating directory %s", path)
        if parents and os.path.dirname(path) not in self:
            self.mkdir(os.path.dirname(path), mode=mode, parents=True)
        self.add_item(Directory(mode, self.user, self.group, path))

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
        for dep_src, dep_dest in find_elf_deps_set(src, self.binroot):
            self.add_file(dep_src, dep_dest)

        # Add file
        if mode is None:
            mode = os.stat(src, follow_symlinks=True).st_mode & 0o7777
        self.add_item(File(mode, self.user, self.group,
                           {dest}, src, hash_file(src)))

    def add_library(self, src: str, dest: Optional[str] = None,
                    mode: Optional[int] = None) -> None:
        """Add a library to the initramfs

        :param src: Path or base name of the library to add,
            if it is not a path, it is searched on the system with
            :func:`find_lib`
        :param dest: Absolute path of the destination, relative to the
            initramfs root, defaults to the path of the source library
        :param mode: File permissions to use, defaults to same as ``src``
        :raises FileNotFoundError: Library or ELF dependency not found
        :raises MergeError: Destination file exists and is different,
            or missing parent directory (raised from :meth:`add_item`)
        """
        lib_src, lib_dest = find_lib(src, root=self.binroot)
        self.add_file(lib_src, dest if dest is not None else lib_dest,
                      mode=mode)

    def add_executable(self, src: str, dest: Optional[str] = None,
                       mode: Optional[int] = None) -> None:
        """Add an executable to the initramfs

        :param src: Path or base name of the executable to add,
            if it is not a path, it is searched on the system with
            :func:`find_exec`
        :param dest: Absolute path of the destination, relative to the
            initramfs root, defaults to the path of the source executable
        :param mode: File permissions to use, defaults to same as ``src``
        :raises FileNotFoundError: Executable or ELF dependency not found
        :raises MergeError: Destination file exists and is different,
            or missing parent directory (raised from :meth:`add_item`)
        """
        exec_src, exec_dest = find_exec(src, root=self.binroot)
        self.add_file(exec_src, dest if dest is not None else exec_dest,
                      mode=mode)

    def add_kmod(self, module: str, mode: Optional[int] = None) -> None:
        """Add a kernel module to the initramfs

        :param module: Path or name of the kernel module to add
        :param mode: File permissions to use, defaults to same as ``module``
        """

        def _add_kmod(module: str, kernel: str) -> None:
            kmod = find_kmod(module, kernel)
            for dep in find_kmod_deps(kmod):
                _add_kmod(dep, kernel)
            self.mkdir(os.path.dirname(kmod), parents=True)
            self.add_file(kmod, mode=mode)

        for kernel in self.kernels:
            _add_kmod(module, kernel)

    def add_busybox(self, needed: Iterable[str] = (),
                    sys_busybox: Optional[str] = None) -> None:
        """Add busybox and its applets to the initramfs

        Applets will be ignored if a file with the same path
        already exists. To avoid collision, this method should be called
        after all needed files have been added.

        If any command listed in ``needed`` is not available in busybox
        (either as an applet, a shell special built-in, or a reserved word),
        the default system one will be included (from ``PATH``).

        :param needed: Needed Busybox compatible shell commands
        :param sys_busybox: Busybox executable to use to get the list of
            applets, defaults to the one in ``PATH``
        :raises FileNotFoundError: Busybox executable or ELF dependency
            not found
        :raises MergeError: Destination file exists and is different,
            or missing parent directory (raised from :meth:`add_item`,
            not raised for applets)
        """
        if sys_busybox is None:
            sys_busybox = find_exec('busybox')[0]
        applets = set() | SHELL_SPECIAL_BUILTIN | SHELL_RESERVED_WORDS

        busybox_src, busybox_dest = find_exec('busybox', root=self.binroot)
        self.add_file(busybox_src, busybox_dest)
        for applet in busybox_get_applets(sys_busybox):
            applets.add(os.path.basename(applet))
            try:
                self.add_file(busybox_src, applet)
            except MergeError:
                logger.debug("Not adding applet %s: file exists", applet)
        for dep in needed:
            if dep not in applets:
                logger.debug("Adding missing applet: %s", dep)
                self.add_executable(dep)

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
