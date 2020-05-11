"""Initramfs functions
This module provides function to build the initramfs. It uses a temporary
directory which it should be the only one to access.

Global variables:
DESTDIR -- Defines the directory in which the initramfs will be built,
  defaults to /tmp/initramfs.
"""

import os
import sys
import stat
import shutil
import glob
import subprocess
import hashlib
import collections

DESTDIR = "/tmp/initramfs"

def mklayout(debug=False):
    """Create the base layout for initramfs
    debug -- bool: Run in debug mode, do not create nodes (allows to be run as
      a non root user)
    Some necessary devices are also created
    This function will fail if DESTDIR exists
    """

    os.makedirs(DESTDIR, mode=0o700, exist_ok=False)

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
    # TODO: Check if tty0 is necessary
    os.mknod(f"{DESTDIR}/dev/console", 0o600 | stat.S_IFCHR, os.makedev(5, 1))
    os.mknod(f"{DESTDIR}/dev/tty", 0o666 | stat.S_IFCHR, os.makedev(5, 0))
    os.mknod(f"{DESTDIR}/dev/null", 0o666 | stat.S_IFCHR, os.makedev(1, 3))
    os.mknod(f"{DESTDIR}/dev/tty0", 0o620 | stat.S_IFCHR, os.makedev(4, 0))

def copyfile(src, dest=None):
    """Copy a file to the initramfs
    If the file is a symlink, it is dereferenced
    src -- String: source, an absolute or relative path
    dest -- String: destination's absolute path without DESTDIR,
      default is the same as source (e.g. /root/file)
    """

    # Configure src and dest
    if not os.path.exists(src):
        raise FileNotFoundError(src)
    src = os.path.abspath(src)
    if not dest:
        dest = src
    # Strip /usr directory, not needed in initramfs
    if "/usr/local/" in dest:
        print(f"Stripping /usr/local/ from {dest}", file=sys.stderr)
        dest = dest.replace("/usr/local", "/")
    elif "/usr/" in dest:
        print(f"Stripping /usr/ from {dest}", file=sys.stderr)
        dest = dest.replace("/usr/", "/")
    # Check destination base directory exists (e.g. /bin)
    if not os.path.isdir(f"{DESTDIR}/{dest.split('/')[1]}"):
        raise FileNotFoundError(f"{DESTDIR}/" + dest.split('/')[1])
    dest = DESTDIR + dest

    if os.path.exists(dest):
        return
    os.makedirs(os.path.dirname(dest), mode=0o755, exist_ok=True)
    shutil.copy(src, dest, follow_symlinks=True)

def findlib(lib):
    """Search a library in the system
    Uses /etc/ld.so.conf, /etc/ld.so.conf.d/*.conf and LD_LIBRARY_PATH
    LD_LIBRARY_PATH contain libdirs separated by ':'
    /etc/ld.so.conf and /etc/ld.so.conf.d/*.conf contains
    one directory per line
    """

    # Get list of directories to search
    libdirs = []
    try:
        for k in os.environ.get('LD_LIBRARY_PATH').split(':'):
            if k:
                libdirs.append(k)
    except AttributeError:
        pass

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

def copylib(src, dest=None):
    """Copy a library to the initramfs
    src -- String: Source library absolute/relative path, or just the name
    dest -- String: destination's absolute path without DESTDIR,
      default is the same as the source path
    """

    # Configure src and dest
    if not os.path.isfile(src):
        src = findlib(src)
    src = os.path.abspath(src)
    copyfile(src, dest)

def findexec(executable):
    """Search an executable within PATH environment variable"""

    # Get set of directories to search
    execdirs = set()
    try:
        for k in os.environ.get('PATH').split(':'):
            if k:
                execdirs.add(k)
    except AttributeError:
        pass

    # Parse directories
    for execdir in execdirs:
        if os.path.isfile(f"{execdir}/{executable}"):
            return f"{execdir}/{executable}"
    raise FileNotFoundError(executable)

def copyexec(src, dest=None):
    """Copy an executable to the initramfs
    src -- String: executable's absolute/relative path, or just
      the name (will be searched in PATH)
    dest -- String: destination's absolute path without DESTDIR,
      default is the same as the source path
    """

    # Configure src and dest
    if not os.path.isfile(src):
        src = findexec(src)
    src = os.path.abspath(src)
    copyfile(src, dest)

    # Find linked libraries
    cmd = subprocess.run(["lddtree", "--list", "--skip-non-elfs", src],
                         stdout=subprocess.PIPE, check=True)
    for lib in cmd.stdout.decode().strip().split('\n'):
        if lib:
            copylib(lib)

def writefile(data, dest, mode=0o644):
    """Write data to a file in initramfs
    data -- Bytes: data to write
    dest -- String: destination's absolute path without DESTDIR
    mode -- File's permissions, defaults to 0o644
    """

    with open(DESTDIR + dest, "wb") as filedest:
        filedest.write(data)
    os.chmod(DESTDIR + dest, mode)

def install_busybox():
    """Create busybox symlinks"""

    cmd = subprocess.run(["busybox", "--list-full"],
                         stdout=subprocess.PIPE, check=True)
    for applet in cmd.stdout.decode().strip().split('\n'):
        if not os.path.exists(f"{DESTDIR}/{applet}"):
            os.symlink("../bin/busybox", f"{DESTDIR}/{applet}")

def mkcpio():
    """Create CPIO archive from initramfs, returns bytes"""
    oldpwd = os.getcwd()
    os.chdir(DESTDIR)
    find = subprocess.run(["find", ".", "-print0"],
                          stdout=subprocess.PIPE, check=True)
    cpio = subprocess.run(["cpio", "--null", "--create", "--format=newc"],
                          input=find.stdout, stdout=subprocess.PIPE, check=True)
    os.chdir(oldpwd)
    return cpio.stdout

def cleanup():
    """Cleanup DESTDIR"""
    shutil.rmtree(DESTDIR)

def hash_file(filepath, chunk_size=65536):
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

def find_duplicates():
    """Generates tuples of duplicated files in DESTDIR"""
    # files_dic: Dictionnary, keys are sha512 hash, value is a list
    # of files sharing this hash
    files_dic = collections.defaultdict(list)
    for root, _, files in os.walk(DESTDIR):
        for filename in files:
            filepath = root + "/" + filename
            if not os.path.islink(filepath):
                files_dic[hash_file(filepath)].append(filepath)

    for key in files_dic:
        if len(files_dic[key]) > 1:
            yield files_dic[key]

def hardlink_duplicates():
    """Hardlink all duplicated files in DESTDIR"""
    for duplicates in find_duplicates():
        print("Hardlinking duplicates " \
              + str([k.replace(DESTDIR,'') for k in duplicates]),
              file=sys.stderr)
        source = duplicates.pop()
        for duplicate in duplicates:
            os.remove(duplicate)
            os.link(source, duplicate)

