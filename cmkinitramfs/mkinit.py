"""Library providing functions and classes to build an init script

``do_foo()`` functions write a string performing the foo action into a
stream. This stream should be the init script.

``_fun_foo()`` functions write a string defining the foo function into a
stream. This stream should be the init script.

The :class:`Data` class defines an abstract object containing data,
it has multiple subclasses for multiple types of data.
The main methods of those classes are :meth:`Data.load`, :meth:`Data.unload`,
and :meth:`Data.set_final`.
Most functions will write into a stream (:term:`text file`) the content
of the init script.
Use a :class:`io.StringIO` if you need to use strings rather than a stream.
"""

from __future__ import annotations

import os.path
from shlex import quote
from typing import FrozenSet, Iterable, IO, List, Optional, Set, Tuple


def _fun_rescue_shell(out: IO[str]) -> None:
    """Define the rescue_shell function

    ``rescue_shell`` takes one argument and drop the user to ``/bin/sh``,
    the argument is the error to print to the user.

    :param out: Stream to write into
    """
    out.writelines((
        "rescue_shell()\n",
        "{\n",
        "\tprintk \"$1\"\n",
        "\techo 'Dropping you into a shell'\n",
        "\texec '/bin/sh'\n",
        "}\n\n",
    ))


def _fun_printk(out: IO[str]) -> None:
    """Define the printk function

    ``printk`` takes one argument and prints it to both the kernel log
    and stderr.

    :param out: Stream to write into
    """
    out.writelines((
        "printk()\n",
        "{\n",
        "\techo \"initramfs: $1\" 1>/dev/kmsg\n",
        "\techo \"$1\" 1>&2\n",
        "}\n\n",
    ))


def _die(message: str, newline: bool = True) -> str:
    """Stop the boot process with an error

    This is a helper calling ``rescue_shell``.

    :param message: Error message to print
    :param newline: Include a newline at the end of the function call
    :return: String stopping the boot proces, the error message will be
        single quoted and escaped
    """
    end = '\n' if newline else ''
    return f"rescue_shell {quote(f'FATAL: {message}')}{end}"


def do_header(out: IO[str], home: str = '/root', path: str = '/bin:/sbin') \
        -> None:
    """Create the /init header

     - Create the shebang ``/bin/sh``
     - Configure environment variables
     - Define ``rescue_shell`` and ``printk``

    :param out: Stream to write into
    :param home: ``HOME`` environment variable
    :param path: ``PATH`` environment variable
    """
    out.writelines((
        "#!/bin/sh\n",
        "\n",
        f"HOME={quote(home)}\n",
        "export HOME\n",
        f"PATH={quote(path)}\n",
        "export PATH\n",
        "\n",
    ))
    _fun_rescue_shell(out)
    _fun_printk(out)
    out.writelines((
        "echo 'INITRAMFS: Start'\n",
        "\n",
    ))


def do_init(out: IO[str]) -> None:
    """Initialize the init environment

     - Check the current PID is 1
     - Mount ``/proc``, ``/sys``, ``/dev``
     - Set the kernel log level to 3

    :param out: Stream to write into
    """
    out.writelines((
        "echo 'Initialization'\n",
        "test $$ -eq 1 || ",
        _die('init expects to be run as PID 1'),
        "mount -t proc none /proc || ",
        _die('Failed to mount /proc'),
        "mount -t sysfs none /sys || ",
        _die('Failed to mount /sys'),
        "mount -t devtmpfs none /dev || ",
        _die('Failed to mount /dev'),
        "echo 3 1>'/proc/sys/kernel/printk'\n",
        "\n",
    ))


def do_cmdline(out: IO[str]) -> None:
    """Parse the kernel command line for known parameters

    Parsed parameters:
     - ``rescue_shell``: Immediately starts a rescue shell
     - ``maintenance``: Starts a rescue shell after mounting rootfs

    :param out: Stream to write into
    """
    out.writelines((
        "for cmdline in $(cat /proc/cmdline); do\n",
        "\tcase \"${cmdline}\" in\n",
        "\t\trescue_shell) rescue_shell 'Manual rescue shell';;\n",
        "\t\tmaintenance) MAINTENANCE=true;;\n",
        "\tesac\n",
        "done\n",
        "\n",
    ))


def do_keymap(out: IO[str], keymap_file: str) -> None:
    """Load a keymap

    :param out: Stream to write into
    :param keymap_file: Absolute path of the file to load
    """
    out.writelines((
        "echo 'Loading keymap'\n",
        f"[ -f {quote(keymap_file)} ] || ",
        _die(f'Failed to load keymap, file {keymap_file} not found'),
        f"loadkmap <{quote(keymap_file)} || ",
        _die(f'Failed to load keymap {keymap_file}'),
        "\n",
    ))


def do_maintenance(out: IO[str]) -> None:
    """Drop to a shell if maintenance mode is enabled

    :param out: Stream to write into
    """
    out.writelines((
        "[ -n \"${MAINTENANCE}\" ] && ",
        "rescue_shell 'Going into maintenance mode'\n",
        "\n",
    ))


def do_switch_root(out: IO[str], init: str, newroot: Data) -> None:
    """Cleanup and switch root

      - Set kernel log level back to boot-time default
      - Unmount ``/dev``, ``/sys``, ``/proc``
      - Switch root

    :param out: Stream to write into
    :param init: Init process to execute from the new root
    :param newroot: Data to use as new root
    """
    out.writelines((
        f"printk 'Run {init} as init process'\n",
        "verb=\"$(awk '{ print $4 }' /proc/sys/kernel/printk)\"\n",
        'echo "${verb}" >/proc/sys/kernel/printk\n',
        "umount /dev || ", _die('Failed to unmount /dev'),
        "umount /proc || ", _die('Failed to unmount /proc'),
        "umount /sys || ", _die('Failed to unmount /sys'),
        "echo 'INITRAMFS: End'\n",
        f"exec switch_root {newroot.path()} {quote(init)}\n",
        "\n",
    ))


class Data:
    """Base class representing any data on the system

    This is an abstract class representing data on the system.
    Its main methods are :meth:`load` and :meth:`unload`.
    :meth:`set_final` declare the object as required for the final
    boot environment (e.g. root fs, usr fs), this will prevent the data
    from being unloaded.

    :param files: Files directly needed in the initramfs.
        Same format as :meth:`deps_files`.
    :param execs: Executables directly needed in the initramfs.
        Same format as :meth:`deps_files`.
    :param libs: Libraries directly needed in the initramfs.
        Same format as :meth:`deps_files`.
    :param _need: Loading and runtime dependencies
    :param _lneed: Loading only dependencies
    :param _needed_by: Reverse dependencies
    :param _is_final: The :class:`Data` should not be unloaded
    :param _is_loaded: The :class:`Data` is currently loaded
    """
    files: Set[Tuple[str, Optional[str]]]
    execs: Set[Tuple[str, Optional[str]]]
    libs: Set[Tuple[str, Optional[str]]]
    _need: List[Data]
    _lneed: List[Data]
    _needed_by: List[Data]
    _is_final: bool
    _is_loaded: bool

    def __init__(self) -> None:
        self.files = set()
        self.execs = set()
        self.libs = set()
        self._need = []
        self._lneed = []
        self._needed_by = []
        self._is_final = False
        self._is_loaded = False

    def deps_files(self) -> Set[Tuple[str, Optional[str]]]:
        """Recursivelly get files needed in the initramfs

        :return: Each file is a tuple in the format (``src``, ``dest``),
            where ``src`` is the source file on the current system,
            and ``dest`` is the destination in the initramfs
            (relative to its root directory).
            If ``dest`` is :data:`None`, then ``src`` is used.
        """
        return self.files.union(
            *(k.deps_files() for k in self._need + self._lneed)
        )

    def deps_execs(self) -> Set[Tuple[str, Optional[str]]]:
        """Recursivelly get executables needed in the initramfs

        :return: Dependencies in the same format as :meth:`deps_files`.
        """
        return self.execs.union(
            *(k.deps_execs() for k in self._need + self._lneed)
        )

    def deps_libs(self) -> Set[Tuple[str, Optional[str]]]:
        """Recursivelly get libraries needed in the initramfs

        :return: Dependencies in the same format as :meth:`deps_files`.
        """
        return self.libs.union(
            *(k.deps_libs() for k in self._need + self._lneed)
        )

    def is_final(self) -> bool:
        """Returns a :class:`bool` indicating if the :class:`Data` is final"""
        return self._is_final

    def set_final(self) -> None:
        """This function set the data object as final

        This means the data is required by the final boot environment
        and should never be unloaded (it would be pointless).
        This will also mark its hard dependencies as final.
        """
        self._is_final = True
        for k in self._need:
            k.set_final()

    def add_dep(self, dep: Data) -> None:
        """Add a :class:`Data` object to the hard dependencies"""
        self._need.append(dep)
        dep._needed_by.append(self)

    def add_load_dep(self, dep: Data) -> None:
        """Add a :class:`Data` object to the loading dependencies"""
        self._lneed.append(dep)
        dep._needed_by.append(self)

    def _pre_load(self, out: IO[str]) -> None:
        """This function does preparation for loading the Data

        Loads all the needed dependencies.
        It should be called from :meth:`load` before the actual loading
        of the data.
        This method *should not* be called if the :class:`Data` is
        already loaded.

        :param out: Stream to write into
        :raises DataError: Already loaded
        """
        if self._is_loaded:
            raise DataError(f"{self} is already loaded")
        self._is_loaded = True
        # Load dependencies
        for k in self._need + self._lneed:
            if not k._is_loaded:
                k.load(out)

    def _post_load(self, out: IO[str]) -> None:
        """This function does post loading cleanup

        If the object is a loading dependency only, it will load all
        its reverse dependencies in order to be unloaded as soon as possible.
        Unloading quickly can be useful when dealing with sensitive data
        (e.g. a LUKS key).
        It should be called from :meth:`load` after the actual loading
        of the data.

        :param out: Stream to write into
        """
        # If not final, load data needing self, this will allow an
        # unloading as soon as possible
        if not self._is_final:
            for k in self._needed_by:
                if not k._is_loaded:
                    k.load(out)
        # Unload data not needed anymore
        for k in self._lneed:
            k._needed_by.remove(self)
            if not k._needed_by:
                k.unload(out)

    def load(self, out: IO[str]) -> None:
        """This function loads the data

        It should be redefined by subclasses,
        this definition is a no-op only dealing with dependencies.

        Before loading, this function should
        load the dependencies with :meth:`_pre_load`.
        After loading, this function should
        unload unnecessary dependencies with :meth:`_post_load`.
        This method *should not* be called if the data is already loaded.

        :param out: Stream to write into
        """
        self._pre_load(out)
        self._post_load(out)

    def _pre_unload(self, out: IO[str]) -> None:
        """This function does pre unloading sanity checks

        It should be called from :meth:`unload` before the actual unloading
        of the data.

        :param out: Stream to write into
        :raises DataError: Not loaded or dependency issue
        """
        if not self._is_loaded:
            raise DataError(f"{self} is not loaded")
        if self._is_final or self._needed_by:
            raise DataError(f"{self} is still needed or not temporary")

    def _post_unload(self, out: IO[str]) -> None:
        """This function does post unloading cleanup

        It removes itself from the :attr:`_needed_by` reverse dependencies
        of all its dependencies, and check if the dependency can be unloaded.
        This method should be called from :meth:`unload` after the actual
        unloading of the data.
        This *should not* be called if the data is not loaded.

        :param out: Stream to write into
        """
        for k in self._need:
            k._needed_by.remove(self)
            if not k._needed_by:
                k.unload(out)
        self._is_loaded = False

    def unload(self, out: IO[str]) -> None:
        """This function unloads data

        It should be redefined by subclasses,
        this definition is a no-op only dealing with dependencies.

        Before unloading, this function should check for any
        dependency error, with :meth:`_pre_unload`.
        After unloading, this function should unload all unneeded
        dependencies, with :meth:`_post_unload`.

        :param out: Stream to write into
        """
        self._pre_unload(out)
        self._post_unload(out)

    def __str__(self) -> str:
        """Get the name of the data

        This string may be quoted with simple quotes in the script.
        This **has** to be implemented by subclasses.
        """
        raise NotImplementedError()

    def path(self) -> str:
        """Get the path of this data

        This function provides a string allowing access to data from within
        the init environment, this string can be a path or a command
        in a subshell (e.g. ``"$(findfs UUID=foobar)"``).
        This string should be ready to be used in the script without
        being quoted nor escaped.
        This **has** to be implemented by subclasses.
        """
        raise NotImplementedError()


class DataError(Exception):
    """Error in the :class:`Data` object"""


class PathData(Data):
    """Absolute path

    :param datapath: Path of the data
    """
    datapath: str

    def __init__(self, datapath: str):
        super().__init__()
        self.datapath = datapath

    def __str__(self) -> str:
        return self.datapath

    def path(self) -> str:
        return quote(self.datapath)


class UuidData(Data):
    """UUID of a data

    The UUID can be a filesystem UUID, or other UUID known by other
    :class:`Data` classes (e.g. a MD UUID).

    :param uuid: UUID of the data
    """
    uuid: str

    def __init__(self, uuid: str):
        super().__init__()
        self.uuid = uuid

    def __str__(self) -> str:
        return "UUID=" + self.uuid

    def path(self) -> str:
        return '"$(findfs ' + quote('UUID=' + self.uuid) + ')"'


class LuksData(Data):
    """LUKS encrypted block device

    :param source: :class:`Data` to unlock (crypto_LUKS volume)
    :param name: Name for the LUKS volume
    :param key: :class:`Data` to use as key file
    :param header: :class:`Data` containing the LUKS header
    :param discard: Enable discards
    """
    source: Data
    name: str
    key: Optional[Data]
    header: Optional[Data]
    discard: bool

    def __init__(self, source: Data, name: str,
                 key: Optional[Data] = None, header: Optional[Data] = None,
                 discard: bool = False):
        super().__init__()
        self.execs.add(('cryptsetup', None))
        self.libs.add(('libgcc_s.so.1', None))
        self.source = source
        self.name = name
        self.key = key
        self.header = header
        self.discard = discard

    def __str__(self) -> str:
        return self.name

    def load(self, out: IO[str]) -> None:
        header = f'--header {self.header.path()} ' if self.header else ''
        key_file = f'--key-file {self.key.path()} ' if self.key else ''
        discard = '--allow-discards ' if self.discard else ''
        self._pre_load(out)
        out.writelines((
            f"echo 'Unlocking LUKS device {self}'\n",
            "cryptsetup ", header, key_file, discard,
            f"open {self.source.path()} {quote(self.name)} || ",
            _die(f'Failed to unlock LUKS device {self}'),
            "\n",
        ))
        self._post_load(out)

    def unload(self, out: IO[str]) -> None:
        self._pre_unload(out)
        out.writelines((
            f"echo 'Closing LUKS device {self}'\n",
            f"cryptsetup close {quote(self.name)} || ",
            _die(f'Failed to close LUKS device {self}'),
            "\n",
        ))
        self._post_unload(out)

    def path(self) -> str:
        return quote('/dev/mapper/' + self.name)


class LvmData(Data):
    """LVM logical volume

    :param vg_name: Name of the volume group
    :param lv_name: Name of the logical volume
    """
    vg_name: str
    lv_name: str

    def __init__(self, vg_name: str, lv_name: str):
        super().__init__()
        self.execs.add(('lvm', None))
        self.vg_name = vg_name
        self.lv_name = lv_name

    def __str__(self) -> str:
        return self.vg_name + "/" + self.lv_name

    def load(self, out: IO[str]) -> None:
        self._pre_load(out)
        out.writelines((
            f"echo 'Enabling LVM logical volume {self}'\n",
            "lvm lvchange --sysinit -a ly ",
            f"{quote(f'{self.vg_name}/{self.lv_name}')} || ",
            _die(f'Failed to enable LVM logical volume {self}'),
            "lvm vgscan --mknodes || ",
            _die(f'Failed to create LVM nodes for {self}'),
            "\n",
        ))
        self._post_load(out)

    def unload(self, out: IO[str]) -> None:
        self._pre_unload(out)
        out.writelines((
            f"echo 'Disabling LVM logical volume {self}'\n",
            "lvm lvchange --sysinit -a ln ",
            f"{quote(f'{self.vg_name}/{self.lv_name}')} || ",
            _die(f'Failed to disable LVM logical volume {self}'),
            "lvm vgscan --mknodes || ",
            _die(f'Failed to remove LVM nodes for {self}'),
            "\n",
        ))
        self._post_unload(out)

    def path(self) -> str:
        # If LV or VG name has an hyphen '-', LVM doubles it in the path
        return quote('/dev/mapper/' + self.vg_name.replace('-', '--')
                     + '-' + self.lv_name.replace('-', '--'))


class MountData(Data):
    """Mount point

    :param source: :class:`Data` to use as source
        (e.g. /dev/sda1, my-luks-data)
    :param mountpoint: Absolute path of the mountpoint
    :param filesystem: Filesystem (used for ``mount -t filesystem``)
    :param options: Mount options
    """
    source: Data
    mountpoint: str
    filesystem: str
    options: str

    def __init__(self, source: Data, mountpoint: str, filesystem: str,
                 options: str = "ro"):
        super().__init__()
        self.source = source if source else PathData("none")
        self.mountpoint = mountpoint
        self.filesystem = filesystem
        self.options = options
        if self.filesystem in ('btrfs',):
            self.execs.add(('btrfs', None))
            self.execs.add(('fsck.btrfs', None))
        elif self.filesystem in ('ext4',):
            self.execs.add(('fsck.ext4', None))
            self.execs.add(('e2fsck', None))
        elif self.filesystem in ('xfs',):
            self.execs.add(('fsck.xfs', None))
            self.execs.add(('xfs_repair', None))
        elif self.filesystem in ('fat', 'vfat'):
            self.execs.add(('fsck.fat', None))
            self.execs.add(('fsck.vfat', None))
        elif self.filesystem in ('exfat',):
            self.execs.add(('fsck.exfat', None))
        elif self.filesystem in ('f2fs',):
            self.execs.add(('fsck.f2fs', None))

    def __str__(self) -> str:
        return self.mountpoint

    def load(self, out: IO[str]) -> None:
        fsck = (
            "FSTAB_FILE=/dev/null ",
            f'fsck -t {quote(self.filesystem)} {self.source.path()} || ',
            _die(f'Failed to check filesystem {self}'),
        ) if self.source.path() != 'none' else ()
        mkdir = (
            f"[ -d {quote(self.mountpoint)} ] || ",
            f"mkdir {quote(self.mountpoint)} || ",
            _die(f'Failed to create directory {self}'),
        ) if os.path.dirname(self.mountpoint) == '/mnt' else ()

        self._pre_load(out)
        out.writelines((
            f"echo 'Mounting filesystem {self}'\n",
            *fsck,
            *mkdir,
            f"mount -t {quote(self.filesystem)} -o {quote(self.options)} ",
            f"{self.source.path()} {quote(self.mountpoint)} || ",
            _die(f'Failed to mount filesystem {self}'),
            "\n",
        ))
        self._post_load(out)

    def unload(self, out: IO[str]) -> None:
        self._pre_unload(out)
        out.writelines((
            f"echo 'Unmounting filesystem {self}'\n",
            f"umount {quote(self.mountpoint)} || ",
            _die(f'Failed to unmount filesystem {self}'),
            "\n",
        ))
        self._post_unload(out)

    def path(self) -> str:
        return quote(self.mountpoint)


class MdData(Data):
    """MD RAID

    :param sources: :class:`Data` to use as sources (e.g. /dev/sda1 and
        /dev/sdb1; or UUID=foo).
    :param name: Name for the MD device
    :raises ValueError: No :class:`Data` source
    """
    sources: FrozenSet[Data]
    name: str

    def __init__(self, sources: Iterable[Data], name: str):
        super().__init__()
        self.execs.add(('mdadm', None))
        self.sources = frozenset(sources)
        self.name = name
        if not self.sources:
            raise ValueError(f"{self} has no source defined")

    def __str__(self) -> str:
        return self.name

    def load(self, out: IO[str]) -> None:
        # Get the string containing all sources to use
        sources: List[str] = []
        for source in self.sources:
            if isinstance(source, UuidData):
                sources.append(f"--uuid {quote(source.uuid)} ")
            else:
                sources.append(f"{source.path()} ")

        self._pre_load(out)
        out.writelines((
            f"echo 'Assembling MD RAID {self}'\n",
            "MDADM_NO_UDEV=1 ",
            "mdadm --assemble ", *sources, f"{quote(self.name)} || ",
            _die(f'Failed to assemble MD RAID {self}'),
            "\n",
        ))
        self._post_load(out)

    def unload(self, out: IO[str]) -> None:
        self._pre_unload(out)
        out.writelines((
            f"echo 'Stopping MD RAID {self}'\n",
            "MDADM_NO_UDEV=1 ",
            f"mdadm --stop {quote(self.name)} || ",
            _die(f'Failed to stop MD RAID {self}'),
            "\n",
        ))
        self._post_unload(out)

    def path(self) -> str:
        return quote('/dev/md/' + self.name)


class CloneData(Data):
    """Clone a :class:`Data` to another

    :param source: :class:`Data` to use as source
    :param dest: :class:`Data` to use as destination
    """
    source: Data
    dest: Data

    def __init__(self, source: Data, dest: Data):
        super().__init__()
        self.source = source
        self.dest = dest

    def __str__(self) -> str:
        return f"{self.source} to {self.dest}"

    def load(self, out: IO[str]) -> None:
        self._pre_load(out)
        out.writelines((
            f"echo 'Cloning {self}'\n",
            f"cp -aT {self.source.path()} {self.dest.path()} || ",
            _die(f'Failed to clone {self}'),
            "\n",
        ))
        self._post_load(out)

    def path(self) -> str:
        return self.dest.path()


def mkinit(out: IO[str], root: Data, mounts: Optional[Iterable[Data]] = None,
           keymap: Optional[str] = None, init: Optional[str] = None) -> None:
    """Create the init script

    :param out: Stream to write into
    :param root: :class:`Data` to use as rootfs
    :param mounts: :class:`Data` needed in addition of rootfs
    :param keymap: Path of the keymap to load, :data:`None` means no keymap
    :param init: Init script to use, defaults to ``/sbin/init``
    """
    if mounts is None:
        mounts = set()
    if init is None:
        init = '/sbin/init'

    do_header(out)
    do_init(out)
    do_cmdline(out)
    if keymap is not None:
        do_keymap(out, keymap)
    root.load(out)
    do_maintenance(out)
    for mount in mounts:
        mount.load(out)
    do_switch_root(out, init, root)
