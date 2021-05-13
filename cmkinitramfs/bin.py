"""Module providing functions to manage binaries and executables

This library also provides utilities like :func:`find_exec`.
Multiple helper functions are also available (e.g. :func:`find_elf_deps_iter`
and :func:`find_elf_deps_set` to list libraries needed by an ELF executable).
"""

from __future__ import annotations

import functools
import glob
import itertools
import logging
import os
import os.path
import platform
import subprocess
from typing import FrozenSet, Iterator, List, Optional, Tuple, Union

from elftools.common.exceptions import ELFError
from elftools.elf.elffile import ELFFile
from elftools.elf.enums import ENUM_DT_FLAGS_1

from .utils import normpath, removeprefix


logger = logging.getLogger(__name__)
#: Kernel modules will be searched in ``{KMOD_DIR}/{KERNEL}/**/*.ko``
KMOD_DIR = '/lib/modules'


class ELFIncompatibleError(ELFError):
    """The ELF files are incompatible"""


def parse_ld_path(ld_path: Optional[str] = None, origin: str = '',
                  root: str = '/') -> Iterator[str]:
    """Parse a colon-delimited list of paths and apply ldso rules

    Note the special handling as dictated by the ldso:
     - Empty paths are equivalent to $PWD
     - $ORIGIN is expanded to the path of the given file, ``origin``

    :param ld_path: Colon-delimited string of paths, defaults to the
        ``LD_LIBRARY_PATH`` environment variable
    :param origin: Directory containing the ELF file being parsed
        (used for $ORIGIN), defaults to an empty string
    :param root: Path to prepend to all paths found
    :return: Iterator over the processed paths
    """

    if ld_path is None:
        ld_path = os.environ.get('LD_LIBRARY_PATH')
        if ld_path is None:
            return
    logger.debug("Parsing ld_path %s", ld_path)

    lib = 'lib64' if platform.architecture()[0] == '64bit' else 'lib'
    platform_ = platform.machine()
    for path in ld_path.split(':'):
        if not path:
            yield normpath(os.getcwd())
        else:
            for k in (('$ORIGIN', origin), ('${ORIGIN}', origin),
                      ('$LIB', lib), ('${LIB}', lib),
                      ('$PLATFORM', platform_), ('${PLATFORM}', platform_)):
                path = path.replace(*k)
            if os.path.isabs(path):
                path = root + '/' + path
            yield normpath(path)


def parse_ld_so_conf_iter(conf_path: Optional[str] = None, root: str = '/') \
        -> Iterator[str]:
    """Parse a ldso config file

    This should handle comments, whitespace, and "include" statements.

    :param conf_path: Path of the ldso config file to parse,
        defaults to ``{root}/etc/ld.so.conf``
    :param root: Path to prepend to all paths found
    :return: Iterator over the processed paths
    """
    if conf_path is None:
        conf_path = normpath(root + '/etc/ld.so.conf')
        if not os.path.isfile(conf_path):
            return
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
                    yield from parse_ld_so_conf_iter(normpath(path), root)
            else:
                yield normpath(root + line)


@functools.lru_cache()
def parse_ld_so_conf_tuple(conf_path: Optional[str] = None, root: str = '/') \
        -> Tuple[str, ...]:
    """Parse a ldso config file

    Cached version of :func:`parse_ld_so_conf_iter`, returning a tuple.

    :param conf_path: Path of the ldso config file to parse,
        defaults to ``{root}/etc/ld.so.conf``
    :param root: Path to prepend to all paths found
    :return: Tuple with the processed paths
    """
    return tuple(parse_ld_so_conf_iter(conf_path, root))


@functools.lru_cache()
def _get_default_libdirs(root: str = '/') -> Tuple[str, ...]:
    """Get the default library directories

    :param root: Root directory to check for library directories
    :return: Libdirs in the initramfs
    """
    libdirs = []
    for lib in ('lib64', 'lib', 'lib32'):
        for prefix in ('/', '/usr/'):
            path = normpath(root + prefix + lib)
            if os.path.exists(path):
                libdirs.append(path)
    return tuple(libdirs)


@functools.lru_cache()
def _get_libdir(arch: int, root: str = '/') -> str:
    """Get the libdir corresponding to a binary class

    :param arch: Binary class (e.g. :data:`32` or :data:`64`)
    :param root: Directory where libdirs are searched
    :return: Libdir in the initramfs
    """
    if arch == 64 and os.path.exists(root + '/lib64'):
        return '/lib64'
    if arch == 32 and os.path.exists(root + '/lib32'):
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
        (len(osabis) == 1 or any(osabis.issubset(x) for x in compat_sets))
        and elf1.elfclass == elf2.elfclass
        and elf1.little_endian == elf2.little_endian
        and elf1.header['e_machine'] == elf2.header['e_machine']
    )


def _get_elf_arch(elf1: Union[ELFFile, str], elf2: Union[ELFFile, str]) -> int:
    """Open elf2, check compatibility, and return ELF architecture

    :param elf1: First ELF
    :param elf2: Second ELF
    :return: Architecture of the ELF files (32 or 64)
    :raises OSError: Could not open an ELF file
    :raises ELFIncompatibleError: ELFs are incompatible
    :raises ELFError: File is not an ELF file
    """

    # Convert string to ELFFile
    if isinstance(elf1, str):
        with open(elf1, 'rb') as elf_file:
            return _get_elf_arch(ELFFile(elf_file), elf2)
    if isinstance(elf2, str):
        with open(elf2, 'rb') as elf_file:
            return _get_elf_arch(elf1, ELFFile(elf_file))
    if not _is_elf_compatible(elf1, elf2):
        raise ELFIncompatibleError("Incompatible ELF binaries")
    return elf1.elfclass


def _find_elf_deps_iter(elf: ELFFile, origin: str, root: str = '/') \
        -> Iterator[Tuple[str, str]]:
    """Iterates over the dependencies of an ELF file

    Backend of :func:`find_elf_deps_iter`.

    :param elf: Elf file to parse
    :param origin: Directory containing the ELF binary (real path as provided
        by :func:`os.path.realpath`, used for $ORIGIN)
    :param root: Path to prepend to all paths found
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
                    rpaths.extend(parse_ld_path(tag.rpath, origin, root))
                elif tag.entry.d_tag == 'DT_RUNPATH':
                    runpaths.extend(parse_ld_path(tag.runpath, origin, root))
                elif tag.entry.d_tag == 'DT_NEEDED':
                    deps.extend(parse_ld_path(tag.needed, origin, root))
                elif tag.entry.d_tag == 'DT_FLAGS_1':
                    if tag.entry.d_val & ENUM_DT_FLAGS_1['DF_1_NODEFLIB']:
                        nodeflib = True

    logger.debug("ELF: deps: %s, rpaths: %s, runpaths: %s, nodeflib: %s",
                 deps, rpaths, runpaths, nodeflib)

    # Directories in which dependencies will be searched
    search_paths_base = tuple(itertools.chain(
        rpaths, parse_ld_path(None, origin, root), runpaths,
        parse_ld_so_conf_tuple(root=root), _get_default_libdirs(root)
    ))

    for dep in deps:

        search_paths = search_paths_base \
            if not os.path.isabs(dep) else (root,)

        # Search the dependency
        for found_dir in search_paths:
            found_path = normpath(found_dir + '/' + dep)

            # Check found_path is valid and compatible
            if nodeflib and found_dir in _get_default_libdirs(root):
                continue
            try:
                found_arch = _get_elf_arch(elf, found_path)
            except (ELFError, OSError):
                continue

            # Lib found
            if os.path.isabs(dep) \
                    or found_dir in itertools.chain(rpaths, runpaths):
                # Libdir in R*PATH: use the same path
                dest = normpath('/' + removeprefix(found_path, root))
            else:
                # Libdir in ld_path or ld.so.conf: use default libdir
                dest = normpath(_get_libdir(found_arch, root) + '/' + dep)
            logger.debug("Found %s in %s (dest: %s)", dep, found_dir, dest)
            yield found_path, dest
            break
        else:
            raise FileNotFoundError(f"ELF dependency not found: {dep}")


def find_elf_deps_iter(src: str, root: str = '/') -> Iterator[Tuple[str, str]]:
    """Iterates over the dependencies of an ELF file

    Read an ELF file to search dynamic library dependencies. For each
    dependency, find it on the system (using RPATH, LD_LIBRARY_PATH,
    RUNPATH, ld.so.conf, and default library directories).

    If the library is in a path encoded in the ELF binary (RPATH or RUNPATH),
    ``dep_dest = dep_src``, otherwise use a default library directory
    according to the type of binary (``/lib``, ``/lib64``, ``/lib32``).

    If the file is not an ELF file, returns an empty iterator.

    :param src: File to find dependencies for
    :param root: Path to prepend to all paths found
    :return: Iterator of ``(dep_src, dep_dest)``, with ``dep_src`` the path
        of the dependency on the current system, and ``dep_dest`` the
        path of the dependency on the initramfs
    :raises FileNotFoundError: Dependency not found
    """
    logger.debug("Searching ELF dependencies for %s", src)

    if src != os.path.realpath(src):
        yield from find_elf_deps_iter(os.path.realpath(src), root)
        return

    with open(src, 'rb') as src_file:
        try:
            elf = ELFFile(src_file)
        except ELFError:
            return
        yield from _find_elf_deps_iter(elf, os.path.dirname(src), root)
    logger.debug("Found all ELF dependencies for %s", src)


@functools.lru_cache()
def find_elf_deps_set(src: str, root: str = '/') -> FrozenSet[Tuple[str, str]]:
    """Find dependencies of an ELF file

    Cached version of :func:`find_elf_deps_iter`.

    :param src: File to find dependencies for
    :param root: Path to prepend to all paths found
    :return: Set of ``(dep_src, dep_dest)``, see :func:`find_elf_deps_iter`.
    :raises FileNotFoundError: Dependency not found
    """
    if src != os.path.realpath(src):
        return find_elf_deps_set(os.path.realpath(src), root)
    return frozenset(find_elf_deps_iter(src, root))


def find_lib_iter(lib: str, compat: Optional[str] = None, root: str = '/') \
        -> Iterator[Tuple[str, str]]:
    """Search a library in the system, with globbing

    Same as :func:`find_lib` but uses :func:`glob.glob` to find matching
    libraries.

    :param lib: Glob pattern for the library to search (e.g. ``libgcc_s.*``)
    :param compat: Path to a binary that the library must be compatible with
        (checked with :func:`_is_elf_compatible`),
        defaults to ``{root}/bin/sh``
    :param root: Path to prepend to all paths found
    :return: Iterator over ``(lib_src, lib_dest)``, see :func:`find_lib`
    :raises FileNotFoundError: Library not found
    """
    if compat is None:
        compat = normpath(root + '/bin/sh')
    logger.debug("Searching library %s (compat: %s)", lib, compat)

    libname = os.path.basename(lib)
    with open(compat, 'rb') as pyexec:
        pyelf = ELFFile(pyexec)

        # If path is absolute: only search in root
        search_paths = itertools.chain(
            (os.getcwd(),),
            parse_ld_path(root=root),
            parse_ld_so_conf_tuple(root=root),
            _get_default_libdirs(root)
        ) if not os.path.isabs(lib) else (root,)

        found = False
        for found_dir in search_paths:
            found_path = found_dir + '/' + libname

            for found_path in glob.iglob(found_path):
                try:
                    found_arch = _get_elf_arch(pyelf, found_path)
                except (ELFError, OSError):
                    continue
                found = True
                dest = normpath(_get_libdir(found_arch, root) + '/' + libname)
                logger.debug("Found %s in %s (dest: %s)", lib, found_dir, dest)
                yield found_path, dest

    if not found:
        raise FileNotFoundError(lib)


def find_lib(lib: str, compat: Optional[str] = None, root: str = '/') \
        -> Tuple[str, str]:
    """Search a library in the system, without globbing

    Uses ``ld.so.conf`` and ``LD_LIBRARY_PATH``.

    Libraries will be installed in the default library directory in the
    initramfs.

    :param lib: Library to search (e.g. ``libgcc_s.so.1``)
    :param compat: Path to a binary that the library must be compatible with
        (checked with :func:`_is_elf_compatible`),
        defaults to ``{root}/bin/sh``
    :param root: Path to prepend to all paths found
    :return: ``(lib_src, lib_dest)``, with ``lib_src`` the absolute path
        of the library on the current system, and ``lib_dest`` the absolute
        path of the library on the initramfs
    :raises FileNotFoundError: Library not found
    """
    return next(find_lib_iter(glob.escape(lib), compat, root))


def parse_path(path: Optional[str] = None, root: str = '/') \
        -> Iterator[str]:
    """Parse PATH variable

    :param path: PATH string to parse,
        default to the ``PATH`` environment variable
    :param root: Path to prepend to all paths found
    :return: Iterator over the processed paths
    """
    if path is None:
        path = os.environ.get('PATH')
        if path is None:
            return
    logger.debug("Parsing path %s", path)

    for k in path.split(':'):
        if not k:
            yield normpath(os.getcwd())
        else:
            yield normpath(root + '/' + k)


def find_exec(executable: str, compat: Optional[str] = None, root: str = '/') \
        -> Tuple[str, str]:
    """Search an executable in the system

    Uses the ``PATH`` environment variable.

    :param executable: Executable to search
    :param compat: Path to a binary that the executable must be compatible with
        (checked with :func:`_is_elf_compatible`),
        defaults to ``{root}/bin/sh``
    :param root: Path to prepend to all paths found
    :return: ``(src_path, dest_path)`` with ``src_path`` the path of the
        executable on ``root``, and ``dest_path`` the default path of the
        executable on the initramfs.
    :return: Absolute path of the executable
    :raises FileNotFoundError: Executable not found
    """
    if compat is None:
        compat = normpath(root + '/bin/sh')
    logger.debug("Searching executable %s (compat: %s)", executable, compat)

    # Get list of directories to search
    execdirs = itertools.chain((os.getcwd(),), parse_path(root=root)) \
        if not os.path.isabs(executable) else (root,)

    # Parse directories
    execname = os.path.basename(executable)
    with open(compat, 'rb') as compat_file:
        compat_elf = ELFFile(compat_file)

        for found_dir in execdirs:
            found_path = normpath(found_dir + '/' + execname)

            # Check for compatibility
            if not os.access(found_path, mode=os.X_OK):
                continue
            try:
                _get_elf_arch(compat_elf, found_path)
            except (ELFIncompatibleError, OSError):
                continue
            except ELFError:
                pass
            dest = normpath('/' + removeprefix(found_path, root))
            logger.debug("Found %s in %s (dest: %s)",
                         executable, found_dir, dest)
            return found_path, dest
    raise FileNotFoundError(executable)


@functools.lru_cache()
def _get_all_kmods(kernel: str) -> FrozenSet[str]:
    """Get all kernel modules on the system

    :param kernel: Target kernel version
    :return: Set with the absolute path of the modules
    """
    return frozenset(glob.glob(normpath(f'{KMOD_DIR}/{kernel}/**/*.ko')))


@functools.lru_cache()
def find_kmod_deps(path: str) -> FrozenSet[str]:
    """Get kernel module dependencies

    :param path: Path of the kernel module to parse
    :return: Set with the dependencies' names
    :raises subprocess.CalledProcessError: Error during ``modinfo``
    """

    cmd = ('modinfo', '-0', '-F', 'depends', path)
    logger.debug("Subprocess: %s", cmd)
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, check=True)
    return frozenset(k for k in proc.stdout.decode('UTF-8').split('\0') if k)


def find_kmod(module: str, kernel: str) -> str:
    """Search a kernel module on the system

    :param module: Name of the kernel module
    :param kernel: Target kernel version
    :return: Absolute path of the kernel module on the system
    :raises FileNotFoundError: Kernel module not found
    """

    logger.debug("Searching module %s for kernel %s", module, kernel)
    if os.path.isabs(module):
        logger.debug("Module path is absolute: %s", module)
        return module
    module_compat = module.replace('_', '-') + '.ko'
    for kmod in _get_all_kmods(kernel):
        if module_compat == os.path.basename(kmod).replace('_', '-'):
            kmod = normpath(kmod)
            logger.debug("Found module %s: %s", module, kmod)
            return kmod
    raise FileNotFoundError(f"Kernel module not found: {module}")
