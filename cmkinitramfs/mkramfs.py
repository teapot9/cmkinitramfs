"""Initramfs functions
This module provides function to build the initramfs. It uses a temporary
directory which it should be the only one to access.

Global variables:
DESTDIR -- Defines the directory in which the initramfs will be built,
  defaults to /tmp/initramfs.
"""

import argparse
import collections
import glob
import hashlib
import logging
import os
import shutil
import stat
import subprocess
import sys
from typing import BinaryIO, Iterator, List, Optional, Set, Tuple

import cmkinitramfs.mkinit as mkinit
import cmkinitramfs.util as util

logger = logging.getLogger(__name__)
DESTDIR = '/tmp/initramfs'


def mklayout(debug: bool = False) -> None:
    """Create the base layout for initramfs
    debug -- bool: Run in debug mode, do not create nodes (allows to be run as
      a non root user)
    Some necessary devices are also created
    This function will fail if DESTDIR exists
    """
    logger.debug("Creating initramfs layout in %s", DESTDIR)

    os.makedirs(DESTDIR, mode=0o755, exist_ok=False)

    os.mkdir(f"{DESTDIR}/bin", mode=0o755)
    os.mkdir(f"{DESTDIR}/dev", mode=0o755)
    os.mkdir(f"{DESTDIR}/etc", mode=0o755)
    os.mkdir(f"{DESTDIR}/mnt", mode=0o755)
    os.mkdir(f"{DESTDIR}/proc", mode=0o555)
    os.mkdir(f"{DESTDIR}/root", mode=0o700)
    os.mkdir(f"{DESTDIR}/run", mode=0o755)
    os.mkdir(f"{DESTDIR}/sbin", mode=0o755)
    os.mkdir(f"{DESTDIR}/sys", mode=0o555)

    # Only create /lib* if they exists on the current system
    for libdir in ["/lib", "/lib32", "/lib64"]:
        if os.path.islink(libdir):
            os.symlink(os.readlink(libdir), f"{DESTDIR}{libdir}")
        elif os.path.isdir(libdir):
            os.mkdir(f"{DESTDIR}{libdir}", mode=0o755)

    if debug:
        return
    os.mknod(f"{DESTDIR}/dev/console", 0o600 | stat.S_IFCHR, os.makedev(5, 1))
    os.mknod(f"{DESTDIR}/dev/tty", 0o666 | stat.S_IFCHR, os.makedev(5, 0))
    os.mknod(f"{DESTDIR}/dev/null", 0o666 | stat.S_IFCHR, os.makedev(1, 3))


def copyfile(src: str, dest: Optional[str] = None,
             deps: bool = True, conflict_ignore: bool = False) -> None:
    """Copy a file to the initramfs
    If the file is a symlink, it is dereferenced
    If the file is an ELF file, its dependencies are also copied
    src -- String: source, an absolute or relative path
    dest -- String: destination's absolute path without DESTDIR,
      default is the same as source (e.g. /root/file)
    deps -- Bool: copy dependencies (for ELF)
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
    # Check destination base directory exists (e.g. /bin)
    if os.path.dirname(dest) != "/" \
            and not os.path.isdir(f"{DESTDIR}/{dest.split('/')[1]}"):
        raise FileNotFoundError(f"{DESTDIR}/{dest.split('/')[1]}")
    dest = DESTDIR + dest

    if os.path.exists(dest):
        if conflict_ignore or hash_file(src) == hash_file(dest):
            logger.debug("File %s has already been copied to %s", src, dest)
            return
        raise FileExistsError(f"Cannot copy {src} to {dest}")

    # Copy dependencies
    if deps:
        for dep in find_elf_deps(src):
            copyfile(dep)

    logger.debug("Copying %s to %s", src, dest)
    os.makedirs(os.path.dirname(dest), mode=0o755, exist_ok=True)
    shutil.copy(src, dest, follow_symlinks=True)


def findlib(lib: str) -> str:
    """Search a library in the system
    Uses /etc/ld.so.conf, /etc/ld.so.conf.d/*.conf and LD_LIBRARY_PATH
    LD_LIBRARY_PATH contain libdirs separated by ':'
    /etc/ld.so.conf and /etc/ld.so.conf.d/*.conf contains
    one directory per line
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
    """Search an executable within PATH environment variable"""
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
    """Find ELF dependencies
    Yields nothing if not an ELF file
    src -- String: elf file path
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
    "Iterates over busybox applets"
    cmd = [busybox_exec, '--list-full']
    with subprocess.Popen(cmd, stdout=subprocess.PIPE) as proc:
        assert proc.stdout is not None
        for line in proc.stdout:
            yield '/' + line.decode().strip()
        if proc.wait() != 0:
            raise subprocess.CalledProcessError(proc.returncode, proc.args)


def mkcpio(dest: BinaryIO) -> None:
    """Create CPIO archive from initramfs
    dest -- File object into which the cpio archive will be writen
    """
    logger.debug("Creating CPIO archive")

    oldpwd = os.getcwd()
    os.chdir(DESTDIR)
    cmd = ["find", ".", "-print0"]
    with subprocess.Popen(cmd, stdout=subprocess.PIPE) as find:
        cmd = ['cpio', '--quiet', '--null', '--create', '--format=newc']
        with subprocess.Popen(cmd, stdin=find.stdout, stdout=dest) as cpio:
            if cpio.wait() != 0:
                raise subprocess.CalledProcessError(cpio.returncode, cpio.args)
        if find.wait() != 0:
            raise subprocess.CalledProcessError(find.returncode, find.args)
    os.chdir(oldpwd)


def cleanup() -> None:
    """Cleanup DESTDIR"""
    logger.debug("Cleaning up %s", DESTDIR)
    if os.path.exists(DESTDIR):
        shutil.rmtree(DESTDIR)


def hash_file(filepath: str, chunk_size: int = 65536) -> bytes:
    """Calculate SHA512 of a given file
    filepath -- String: path of the file to hash
    chunk_size -- Number of bytes per chunk of file to hash
    Return the hash in a byte object
    """
    sha512 = hashlib.sha512()
    with open(filepath, 'rb') as src:
        for chunk in iter(lambda: src.read(chunk_size), b''):
            sha512.update(chunk)
    return sha512.digest()


def find_duplicates() -> Iterator[List[str]]:
    """Generates tuples of duplicated files in DESTDIR"""
    # files_dic: Dictionnary, keys are sha512 hash, value is a list
    # of files sharing this hash
    files_dic = collections.defaultdict(list)
    for root, _, files in os.walk(DESTDIR):
        for filename in files:
            filepath = root + "/" + filename
            if os.path.isfile(filepath) and not os.path.islink(filepath):
                files_dic[hash_file(filepath)].append(filepath)

    for key in files_dic:
        if len(files_dic[key]) > 1:
            yield files_dic[key]


def hardlink_duplicates() -> None:
    """Hardlink all duplicated files in DESTDIR"""
    for duplicates in find_duplicates():
        logger.debug("Hardlinking duplicates %s",
                     [k.replace(DESTDIR, '') for k in duplicates])
        source = duplicates.pop()
        for duplicate in duplicates:
            os.remove(duplicate)
            os.link(source, duplicate)


def mkinitramfs(
        init_str: str,
        files: Optional[Set[Tuple[str, Optional[str]]]] = None,
        execs: Optional[Set[Tuple[str, Optional[str]]]] = None,
        libs: Optional[Set[Tuple[str, Optional[str]]]] = None,
        keymap_src: Optional[str] = None,
        keymap_dest: Optional[str] = None,
        output: Optional[str] = None,
        force_cleanup: bool = False,
        debug: bool = False,
        ) -> None:
    """Create the initramfs"""

    if files is None:
        files = set()
    if execs is None:
        execs = set()
    if libs is None:
        libs = set()
    if keymap_dest is None:
        keymap_dest = '/root/keymap.bmap'
    if output is None:
        output = '/usr/src/initramfs.cpio'

    # Cleanup and initialization
    if force_cleanup:
        logger.warning("Overwriting temporary directory %s", DESTDIR)
        cleanup()
    logger.info("Building initramfs in %s", DESTDIR)
    mklayout(debug=debug)

    # /init
    logger.info("Generating /init")
    with open(f'{DESTDIR}/init', 'wt') as dest:
        dest.write(init_str)
    os.chmod(f'{DESTDIR}/init', 0o755)

    # Copy files, execs, libs
    logger.info("Copying files %s", [k[0] for k in files])
    for fsrc, fdest in files:
        copyfile(fsrc, fdest)
    logger.info("Copying executables %s", [k[0] for k in execs])
    for fsrc, fdest in execs:
        copyfile(findexec(fsrc), fdest)
    logger.info("Copying libraries %s", [k[0] for k in libs])
    for fsrc, fdest in libs:
        copyfile(findlib(fsrc), fdest)

    # Copy keymap
    if keymap_src is not None:
        logger.info("Copying keymap %s to %s", keymap_src, keymap_dest)
        gzip_cmd = ['gzip', '-kdc', keymap_src]
        loadkeys_cmd = ['loadkeys', '--bkeymap']
        with subprocess.Popen(gzip_cmd, stdout=subprocess.PIPE) as gzip, \
                open(f'{DESTDIR}/{keymap_dest}', 'wb') as keymap_dest_f, \
                subprocess.Popen(loadkeys_cmd, stdin=gzip.stdout,
                                 stdout=keymap_dest_f) as loadkeys:
            if loadkeys.wait() != 0:
                raise subprocess.CalledProcessError(loadkeys.returncode,
                                                    loadkeys.args)
            if gzip.wait() != 0:
                raise subprocess.CalledProcessError(gzip.returncode,
                                                    gzip.args)
        os.chmod(f'{DESTDIR}/{keymap_dest}', 0o644)

    # Busybox
    logger.info("Installing busybox")
    busybox = findexec('busybox')
    copyfile(busybox)
    for applet in busybox_get_applets(busybox):
        copyfile(busybox, applet, deps=False, conflict_ignore=True)

    # Hardlink duplicate files
    logger.info("Hardlinking duplicates")
    hardlink_duplicates()

    # Create initramfs
    logger.info("Building CPIO archive %s", output)
    if output == "-":
        mkcpio(sys.stdout.buffer)
    else:
        with open(output, 'wb') as cpiodest:
            mkcpio(cpiodest)
        logger.debug("%s bytes copied", os.path.getsize(output))

    if not debug:
        logger.info("Cleaning up temporary files")
        cleanup()


def entry_point() -> None:
    """Main entry point"""
    parser = argparse.ArgumentParser(description="Build an initramfs.")
    parser.add_argument(
        "--debug", "-d", action="store_true", default=False,
        help="debugging mode: non-root, does not cleanup the build directory"
    )
    parser.add_argument(
        "--output", "-o", type=str, default=None,
        help="set output cpio file"
    )
    parser.add_argument(
        "--clean", "-C", action="store_true", default=False,
        help="overwrite temporary directory if it exists"
    )
    parser.add_argument(
        '--verbose', '-v', action='store_true', default=False,
        help="be verbose",
    )
    parser.add_argument(
        '--quiet', '-q', action='count', default=0,
        help="be quiet (can be repeated)",
    )
    args = parser.parse_args()

    if args.verbose:
        level = logging.DEBUG
    elif args.quiet >= 3:
        level = logging.CRITICAL
    elif args.quiet >= 2:
        level = logging.ERROR
    elif args.quiet >= 1:
        level = logging.WARNING
    else:
        level = logging.INFO
    logging.getLogger().setLevel(level)

    config = util.read_config()
    if config['build_dir'] is not None:
        global DESTDIR
        DESTDIR = config['build_dir']
    mkinitramfs(
        # init_str
        mkinit.mkinit(
            root=config['root'], mounts=config['mounts'],
            keymap=(None if config['keymap_src'] is None
                    else '' if config['keymap_dest'] is None
                    else config['keymap_dest']),
            init=config['init']
        ),
        # args from config
        files=config['files'],
        execs=config['execs'],
        libs=config['libs'],
        keymap_src=config['keymap_src'],
        keymap_dest=config['keymap_dest'],
        # args from cmdline
        output=(args.output if args.output is not None else config['output']),
        force_cleanup=args.clean,
        debug=args.debug,
    )
