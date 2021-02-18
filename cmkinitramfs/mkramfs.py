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
import lzma
import os
import shutil
import stat
import subprocess
import sys
from typing import BinaryIO, Iterator, List, Optional, Set

import cmkinitramfs.mkinit as mkinit
import cmkinitramfs.util as util

logger = logging.getLogger(__name__)
DESTDIR = "/tmp/initramfs"
QUIET = False


def mklayout(debug: bool = False) -> None:
    """Create the base layout for initramfs
    debug -- bool: Run in debug mode, do not create nodes (allows to be run as
      a non root user)
    Some necessary devices are also created
    This function will fail if DESTDIR exists
    """

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


def copyfile(src: str, dest: Optional[str] = None, deps: bool = True) -> None:
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
        logger.info("Stripping /usr/local/ from %s", dest)
        dest = dest.replace("/usr/local", "/")
    elif "/usr/" in dest:
        logger.info("Stripping /usr/ from %s", dest)
        dest = dest.replace("/usr/", "/")
    # Check destination base directory exists (e.g. /bin)
    if os.path.dirname(dest) != "/" \
            and not os.path.isdir(f"{DESTDIR}/{dest.split('/')[1]}"):
        raise FileNotFoundError(f"{DESTDIR}/{dest.split('/')[1]}")
    dest = DESTDIR + dest

    if os.path.exists(dest):
        return

    # Copy dependencies
    if deps:
        for dep in find_elf_deps(src):
            copyfile(dep)

    os.makedirs(os.path.dirname(dest), mode=0o755, exist_ok=True)
    shutil.copy(src, dest, follow_symlinks=True)


def findlib(lib: str) -> str:
    """Search a library in the system
    Uses /etc/ld.so.conf, /etc/ld.so.conf.d/*.conf and LD_LIBRARY_PATH
    LD_LIBRARY_PATH contain libdirs separated by ':'
    /etc/ld.so.conf and /etc/ld.so.conf.d/*.conf contains
    one directory per line
    """

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


def install_busybox() -> None:
    """Create busybox symlinks"""

    busybox = findexec("busybox")
    cmd = ['busybox', '--list-full']
    with subprocess.Popen(cmd, stdout=subprocess.PIPE) as proc:
        assert proc.stdout is not None
        for line in proc.stdout:
            copyfile(busybox, '/' + line.decode().strip(), deps=False)
        if proc.wait() != 0:
            raise subprocess.CalledProcessError(proc.returncode, proc.args)


def mkcpio(dest: BinaryIO) -> None:
    """Create CPIO archive from initramfs
    dest -- File object into which the cpio archive will be writen
    """
    oldpwd = os.getcwd()
    os.chdir(DESTDIR)
    cmd = ["find", ".", "-print0"]
    with subprocess.Popen(cmd, stdout=subprocess.PIPE) as find:
        cmd = ['cpio', '--null', '--create', '--format=newc']
        with subprocess.Popen(cmd, stdin=find.stdout, stdout=dest) as cpio:
            if cpio.wait() != 0:
                raise subprocess.CalledProcessError(cpio.returncode, cpio.args)
        if find.wait() != 0:
            raise subprocess.CalledProcessError(find.returncode, find.args)
    os.chdir(oldpwd)


def cleanup() -> None:
    """Cleanup DESTDIR"""
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
        logger.info("Hardlinking duplicates %s",
                    [k.replace(DESTDIR, '') for k in duplicates])
        source = duplicates.pop()
        for duplicate in duplicates:
            os.remove(duplicate)
            os.link(source, duplicate)


def mkinitramfs(
        init_str: str,
        data_types: Optional[Set[str]] = None,
        filesystems: Optional[Set[str]] = None,
        user_files: Optional[Set[str]] = None,
        keymap_src: Optional[str] = None,
        keymap_dest: str = '/root/keymap.bmap',
        output: str = '/usr/src/initramfs.cpio',
        kernel: str = 'linux',
        force_cleanup: bool = False,
        debug: bool = False,
        ) -> None:
    """Create the initramfs"""
    if data_types is None:
        logger.warning("No data types selected")
        data_types = set()
    if filesystems is None:
        logger.warning("No filesystem selected")
        filesystems = set()
    if user_files is None:
        logger.info("No user file selected")
        user_files = set()

    # Cleanup and initialization
    if force_cleanup:
        logger.warning("Overwriting temporary directory %s", DESTDIR)
        cleanup()
    logger.info("Building initramfs")
    mklayout(debug=debug)

    # Copy user files
    for filepath in user_files:
        logger.info("Copying %s to /root", filepath)
        copyfile(filepath)

    # Busybox
    logger.info("Installing busybox")
    copyfile(findexec('busybox'))
    install_busybox()

    # Files for data types and filesystems

    if "luks" in data_types:
        logger.info("Installing LUKS utils")
        copyfile(findexec("cryptsetup"))
        copyfile(findlib("libgcc_s.so.1"))

    if "lvm" in data_types:
        logger.info("Installing LVM utils")
        copyfile(findexec("lvm"))

    if "md" in data_types:
        logger.info("Installing MD utils")
        copyfile(findexec("mdadm"))

    if "btrfs" in filesystems:
        logger.info("Installing BTRFS utils")
        copyfile(findexec("btrfs"))
        copyfile(findexec("fsck.btrfs"))

    if "ext4" in filesystems:
        logger.info("Installing EXT4 utils")
        copyfile(findexec("fsck.ext4"))
        copyfile(findexec("e2fsck"))

    if "xfs" in filesystems:
        logger.info("Installing XFS utils")
        copyfile(findexec("fsck.xfs"))
        copyfile(findexec("xfs_repair"))

    if "fat" in filesystems or "vfat" in filesystems:
        logger.info("Installing FAT utils")
        copyfile(findexec("fsck.fat"))
        copyfile(findexec("fsck.vfat"))

    if "f2fs" in filesystems:
        logger.info("Installing F2FS utils")
        copyfile(findexec("fsck.f2fs"))

    if keymap_src:
        logger.info("Copying keymap to %s", keymap_dest)
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

    logger.info("Generatine /init")
    with open(f'{DESTDIR}/init', 'wt') as dest:
        dest.write(init_str)
    os.chmod(f'{DESTDIR}/init', 0o755)

    logger.info("Hardlinking duplicated files")
    hardlink_duplicates()

    # Create initramfs
    if not debug:
        logger.info("Building CPIO archive")
        if output == "-":
            mkcpio(sys.stdout.buffer)
        else:
            with open(output, 'wb') as cpiodest:
                mkcpio(cpiodest)

        logger.info("Cleaning up temporary files")
        cleanup()

        # Cleanup kernel's initramfs
        if kernel != "none":
            logger.info("Cleaning up kernel %s", kernel)
            rm = glob.glob(f"/usr/src/{kernel}/usr/initramfs_data.cpio*")
            for fname in rm:
                os.remove(fname)

        if not output:
            logger.info("Installing initramfs to /boot")
            if os.path.isfile("/boot/initramfs.cpio.xz"):
                shutil.copyfile("/boot/initramfs.cpio.xz",
                                "/boot/initramfs.cpio.xz.old")
            with open('/usr/src/initramfs.cpio', 'rb') as cpio,  \
                    lzma.open('/boot/initramfs.cpio.xz', 'wb',
                              format=lzma.FORMAT_XZ) as cpioxz:
                shutil.copyfileobj(cpio, cpioxz)


def _find_config_file() -> Optional[str]:
    """Find a configuration file to use"""
    if os.environ.get('CMKINITCFG'):
        return os.environ['CMKINITCFG']
    if os.path.isfile('./cmkinitramfs.ini'):
        return './cmkinitramfs.ini'
    if os.path.isfile('/etc/cmkinitramfs.ini'):
        return '/etc/cmkinitramfs.ini'
    return None


def entry_point() -> None:
    """Main entry point"""
    parser = argparse.ArgumentParser(description="Build an initramfs.")
    parser.add_argument(
        "--debug", "-d", action="store_true", default=False,
        help="Enable debugging mode: non root and does not create final archive"
    )
    parser.add_argument(
        "--output", "-o", type=str, default='/usr/src/initramfs.cpio',
        help="Set output cpio file and disable writing to /boot"
    )
    parser.add_argument(
        "--clean", "-C", action="store_true", default=False,
        help="Overwrite temporary directory if it exists"
    )
    parser.add_argument(
        "kernel", type=str, nargs='?', default='linux',
        help="Select kernel version to cleanup rather than /usr/src/linux"
    )
    parser.add_argument(
        '--verbose', '-v', action='store_true', default=False,
        help="Be verbose",
    )
    parser.add_argument(
        '--quiet', '-q', action='store_true', default=False,
        help="Only show errors",
    )
    args = parser.parse_args()

    if args.debug:
        level = logging.DEBUG
    elif args.verbose:
        level = logging.INFO
    elif args.quiet:
        level = logging.ERROR
    else:
        level = logging.WARNING
    logging.getLogger().setLevel(level)

    config = util.read_config()
    mkinitramfs(
        # init_str
        mkinit.mkinit(
            root=config['root'], mounts=config['mounts'],
            keymap_src=config['keymap_src'], keymap_dest=config['keymap_dest'],
            init=config['init']
        ),
        # args from config
        data_types=config['data_types'],
        filesystems=config['filesystems'],
        user_files=config['user_files'],
        keymap_src=config['keymap_src'],
        keymap_dest=config['keymap_dest'],
        # args from cmdline
        output=args.output,
        kernel=args.kernel,
        force_cleanup=args.clean,
        debug=args.debug,
    )
