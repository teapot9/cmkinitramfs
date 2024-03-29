"""Module providing the Data class for init script data sources management

The :class:`Data` class defines an abstract object containing data,
it has multiple subclasses for multiple types of data.
The main methods of those classes are :meth:`Data.load`, :meth:`Data.unload`,
and :meth:`Data.set_final`.

Most functions will write into a stream (:term:`text file`) the content
of the init script.
Use a :class:`io.StringIO` if you need to use strings rather than a stream.
"""

from __future__ import annotations

import itertools
import os.path
from shlex import quote
from typing import Iterable, Iterator, IO, List, Optional, Set, Tuple


class Data:
    """Base class representing any data on the system

    This is an abstract class representing data on the system.
    Its main methods are :meth:`load` and :meth:`unload`.
    :meth:`set_final` declare the object as required for the final
    boot environment (e.g. root fs, usr fs), this will prevent the data
    from being unloaded.

    :param files: Files directly needed in the initramfs.
        Each file is a tuple in the format (``src``, ``dest``),
        where ``src`` is the source file on the current system,
        and ``dest`` is the destination in the initramfs
        (relative to its root directory).
        If ``dest`` is :data:`None`, then ``src`` is used.
    :param execs: Executables directly needed in the initramfs.
        Same format as :attr:`files`.
    :param libs: Libraries directly needed in the initramfs.
        Same format as :attr:`files`.
    :param busybox: Busybox compatible commands needed in the initramfs.
        Any commands that are compatible with Busybox's implementation
        should be added.
        Exception: special shell built-in commands and reserved words
        are guaranteed to be available and *can* be ommitted
        (a list is defined
        in :data:`cmkinitramfs.initramfs.SHELL_SPECIAL_BUILTIN`
        and :data:`cmkinitramfs.initramfs.SHELL_RESERVED_WORDS`).
    :param kmods: Kernel modules directly needed in the initramfs.
        Each module is a tuple in the format ``(module, params)``,
        where ``params`` is a tuple of module parameters (may be empty).
    :param _need: Loading and runtime dependencies
    :param _lneed: Loading only dependencies
    :param _needed_by: Reverse dependencies
    :param _is_final: The :class:`Data` should not be unloaded
    :param _is_loaded: The :class:`Data` is currently loaded
    """
    files: Set[Tuple[str, Optional[str]]]
    execs: Set[Tuple[str, Optional[str]]]
    libs: Set[Tuple[str, Optional[str]]]
    busybox: Set[str]
    kmods: Set[Tuple[str, Tuple[str, ...]]]
    _need: List[Data]
    _lneed: List[Data]
    _needed_by: List[Data]
    _is_final: bool
    _is_loaded: bool

    @classmethod
    def initialize(cls, out: IO[str]) -> None:
        """Initialize the data class

        Initialize the environment for the use of this data class:
        define needed functions and variables.
        A Data class should be initialized only once in the init script,
        before the first :meth:`Data.load` call of the class.

        Default initialization is a no-op and should be redefined
        by subclasses. Subclasses should call their parent :class:`Data`
        class' :meth:`Data.initialize` method.

        :param out: Stream to write into
        """

    def __init__(self) -> None:
        self.files = set()
        self.execs = set()
        self.libs = set()
        self.busybox = set()
        self.kmods = set()
        self._need = []
        self._lneed = []
        self._needed_by = []
        self._is_final = False
        self._is_loaded = False

    def __eq__(self, other: object) -> bool:
        return isinstance(other, type(self)) and self.files == other.files \
            and self.execs == other.execs and self.libs == other.libs \
            and self._need == other._need and self._lneed == other._lneed \
            and self._is_final == other._is_final

    def iter_all_deps(self) -> Iterator[Data]:
        """Recursivelly get dependencies

        :return: Iterator over all the dependencies
        """
        for dep in itertools.chain(self._need, self._lneed):
            yield dep
            yield from dep.iter_all_deps()

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
        if dep in self._lneed:
            self._lneed.remove(dep)
        if dep not in self._need:
            self._need.append(dep)
        if self not in dep._needed_by:
            dep._needed_by.append(self)

    def add_load_dep(self, dep: Data) -> None:
        """Add a :class:`Data` object to the loading dependencies"""
        if dep not in self._lneed and dep not in self._need:
            self._lneed.append(dep)
        if self not in dep._needed_by:
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

    def __eq__(self, other: object) -> bool:
        return isinstance(other, type(self)) and super().__eq__(other) \
            and self.datapath == other.datapath

    def path(self) -> str:
        return quote(self.datapath)


class UuidData(Data):
    """UUID of a data

    The UUID can be a filesystem UUID, or other UUID known by other
    :class:`Data` classes (e.g. a MD UUID).

    :param uuid: UUID of the data
    :param partition: If :data:`True`, the UUID is treated as a partition UUID
    """
    uuid: str
    partition: bool

    def __init__(self, uuid: str, partition: bool = False):
        super().__init__()
        if partition:
            # PARTUUID is only available in util-linux findfs
            self.execs |= {('findfs', None)}
        else:
            self.busybox |= {'findfs'}
        self.uuid = uuid
        self.partition = partition

    def __str__(self) -> str:
        return ('PARTUUID=' if self.partition else 'UUID=') + self.uuid

    def __eq__(self, other: object) -> bool:
        return isinstance(other, type(self)) and super().__eq__(other) \
            and self.uuid == other.uuid and self.partition == other.partition

    def path(self) -> str:
        prefix = 'PARTUUID=' if self.partition else 'UUID='
        return '"$(findfs ' + quote(prefix + self.uuid) + ')"'


class LabelData(Data):
    """Label of a data

    The label can be a filesystem or partition label, or a label known
    by other :class:`Data` classes.

    :param label: Label of the data
    :param partition: If :data:`True`, the label is treated as a partition
        label
    """
    label: str
    partition: bool

    def __init__(self, label: str, partition: bool = False):
        super().__init__()
        if partition:
            # PARTLABEL is only available in util-linux findfs
            self.execs |= {('findfs', None)}
        else:
            self.busybox |= {'findfs'}
        self.label = label
        self.partition = partition

    def __str__(self) -> str:
        return ('PARTLABEL=' if self.partition else 'LABEL=') + self.label

    def __eq__(self, other: object) -> bool:
        return isinstance(other, type(self)) and super().__eq__(other) \
            and self.label == other.label and self.partition == other.partition

    def path(self) -> str:
        prefix = 'PARTLABEL=' if self.partition else 'LABEL='
        return '"$(findfs ' + quote(prefix + self.label) + ')"'


class LuksData(Data):
    """LUKS encrypted block device

    :param source: :class:`Data` to unlock (crypto_LUKS volume),
        it will be set as a hard dependency
    :param name: Name for the LUKS volume
    :param key: :class:`Data` to use as key file,
        it will be set as a load dependency
    :param header: :class:`Data` containing the LUKS header,
        it will be set as a load dependency
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
        self.kmods.add(('dm-crypt', ()))
        self.source = source
        self.name = name
        self.key = key
        self.header = header
        self.discard = discard
        self.add_dep(self.source)
        if self.key:
            self.add_load_dep(self.key)
        if self.header:
            self.add_load_dep(self.header)

    def __str__(self) -> str:
        return self.name

    def __eq__(self, other: object) -> bool:
        return isinstance(other, type(self)) and super().__eq__(other) \
            and self.source == other.source and self.name == other.name \
            and self.key == other.key and self.header == other.header \
            and self.discard == other.discard

    def load(self, out: IO[str]) -> None:
        header = f'--header {self.header.path()} ' if self.header else ''
        key_file = f'--key-file {self.key.path()} ' if self.key else ''
        discard = '--allow-discards ' if self.discard else ''
        self._pre_load(out)
        out.writelines((
            f"info 'Unlocking LUKS device {self}'\n",
            "cryptsetup ", header, key_file, discard,
            f"open {self.source.path()} {quote(self.name)} || die ",
            quote(f'Failed to unlock LUKS device {self}'), '\n',
            "\n",
        ))
        self._post_load(out)

    def unload(self, out: IO[str]) -> None:
        self._pre_unload(out)
        out.writelines((
            f"info 'Closing LUKS device {self}'\n",
            f"cryptsetup close {quote(self.name)} || die ",
            quote(f'Failed to close LUKS device {self}'), '\n',
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

    @staticmethod
    def __lvm_conf(out: IO[str]) -> None:
        """Create LVM config in ``/etc/lvm/lvmlocal.conf``

        This override some configurations specific to the
        initramfs environment.

        Note: if ``/etc/lvm/lvmlocal.conf`` exists, we append to it,
        which may cause duplicate configuration warnings from LVM.
        """
        out.writelines((
            "debug 'Writing LVM configuration'\n",
            "mkdir -p /etc/lvm && touch /etc/lvm/lvmlocal.conf || warn ",
            "'Failed to create LVM configuration file'\n",
            "{\n",
            "\techo 'activation/monitoring = 0'\n",
            "\techo 'activation/udev_rules = 0'\n",
            "\techo 'activation/udev_sync = 0'\n",
            "\techo 'devices/external_device_info_source = \"none\"'\n",
            "\techo 'devices/md_component_detection = 0'\n",
            "\techo 'devices/multipath_component_detection = 0'\n",
            "\techo 'devices/obtain_device_list_from_udev = 0'\n",
            "\techo 'global/locking_type = 4'\n",
            "\techo 'global/use_lvmetad = 0'\n",
            "\techo 'global/use_lvmlockd = 0'\n",
            "\techo 'global/use_lvmpolld = 0'\n",
            "} >>/etc/lvm/lvmlocal.conf || warn ",
            "'Failed to write LVM configuration file'\n"
            "\n",
        ))

    @classmethod
    def initialize(cls, out: IO[str]) -> None:
        super().initialize(out)
        LvmData.__lvm_conf(out)

    def __init__(self, vg_name: str, lv_name: str):
        super().__init__()
        self.execs.add(('lvm', None))
        self.vg_name = vg_name
        self.lv_name = lv_name

    def __str__(self) -> str:
        return self.vg_name + "/" + self.lv_name

    def __eq__(self, other: object) -> bool:
        return isinstance(other, type(self)) and super().__eq__(other) \
            and self.vg_name == other.vg_name and self.lv_name == other.lv_name

    def load(self, out: IO[str]) -> None:
        self._pre_load(out)
        out.writelines((
            f"info 'Enabling LVM logical volume {self}'\n",
            "lvm lvchange --sysinit -a ly ",
            f"{quote(f'{self.vg_name}/{self.lv_name}')} || die ",
            quote(f'Failed to enable LVM logical volume {self}'), '\n',
            "lvm vgmknodes || err ",
            quote(f'Failed to create LVM nodes for {self}'), '\n',
            "\n",
        ))
        self._post_load(out)

    def unload(self, out: IO[str]) -> None:
        self._pre_unload(out)
        out.writelines((
            f"info 'Disabling LVM logical volume {self}'\n",
            "lvm lvchange --sysinit -a ln ",
            f"{quote(f'{self.vg_name}/{self.lv_name}')} || die ",
            quote(f'Failed to disable LVM logical volume {self}'), '\n',
            "lvm vgmknodes || err ",
            quote(f'Failed to remove LVM nodes for {self}'), '\n',
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
        (e.g. /dev/sda1, my-luks-data),
        it will be set as a hard dependency
    :param mountpoint: Absolute path of the mountpoint
    :param filesystem: Filesystem (used for ``mount -t filesystem``)
    :param options: Mount options
    """
    source: Data
    mountpoint: str
    filesystem: str
    options: str

    @staticmethod
    def __fun_fsck(out: IO[str]) -> None:
        """Define the mount_fsck function

        This function takes any number of arguments, which will be passed
        to ``fsck``. This function checks the return code of the ``fsck``
        command and acts accordingly.

        This functions calls fsck with ``$@``.
        It checks the return code of ``fsck`` and :

        - No error: returns 0.
        - Non fatal error: prints an error and returns 0.
        - Non fatal error requiring reboot: prints an error and reboot.
        - Fatal error: returns 1.

        :param out: Stream to write into
        """
        fsck_err = {
            1: ('notice', "Filesystem errors corrected"),
            2: ('notice', "System should be rebooted"),
            4: ('alert', "Filesystem errors left uncorrected"),
            8: ('crit', "Operational error"),
            16: ('crit', "Usage or syntax error"),
            32: ('err', "Checking canceled by user request"),
            128: ('crit', "Shared-library error"),
        }
        code_err = 4 | 8 | 16 | 32 | 64 | 128
        code_reboot = 2

        out.writelines((
            'mount_fsck()\n',
            '{\n',
            '\tFSTAB_FILE=/dev/null "$@"\n',
            '\tfsck_ret=$?\n'
            '\t[ "${fsck_ret}" -eq 0 ] && return 0\n',
        ))
        for err_code, err_data in fsck_err.items():
            err_call, err_str = err_data
            out.writelines((
                f'\t[ "$((fsck_ret & {err_code}))" -eq {err_code} ] && ',
                err_call, ' ', quote(f"fsck: {err_str}"), '\n',
            ))
        out.writelines((
            '\t[ "$((fsck_ret & ', str(code_err), '))" -ne 0 ] && return 1\n',
            '\tif [ "$((fsck_ret & ', str(code_reboot), '))" -eq 2 ]; then ',
            'notice \'Rebooting...\'; reboot -f; fi\n',
            '\treturn 0\n',
            '}\n',
            '\n',
        ))

    @staticmethod
    def mkdir(path: str, fatal: bool = False) -> Iterable[str]:
        """Create a directory"""
        return (
            f'[ -d {quote(path)} ] || mkdir {quote(path)} || ',
            'die ' if fatal else 'err ',
            quote(f'Failed to create directory {quote(path)}'), '\n',
        )

    @classmethod
    def initialize(cls, out: IO[str]) -> None:
        super().initialize(out)
        MountData.__fun_fsck(out)

    def __init__(self, source: Data, mountpoint: str, filesystem: str,
                 options: str = "ro"):
        super().__init__()
        self.busybox |= {'fsck', '[', 'reboot', 'mkdir', 'mount', 'umount'}
        self.source = source if source else PathData("none")
        self.mountpoint = mountpoint
        self.filesystem = filesystem
        self.options = options
        if self.filesystem in ('btrfs',):
            self.execs.add(('btrfs', None))
            self.execs.add(('fsck.btrfs', None))
            self.kmods.add(('btrfs', ()))
        elif self.filesystem in ('ext4',):
            self.execs.add(('fsck.ext4', None))
            self.execs.add(('e2fsck', None))
            self.kmods.add(('ext4', ()))
        elif self.filesystem in ('xfs',):
            self.execs.add(('fsck.xfs', None))
            self.execs.add(('xfs_repair', None))
            self.kmods.add(('xfs', ()))
        elif self.filesystem in ('fat', 'vfat'):
            self.execs.add(('fsck.fat', None))
            self.execs.add(('fsck.vfat', None))
            self.kmods.add(('vfat', ()))
        elif self.filesystem in ('exfat',):
            self.execs.add(('fsck.exfat', None))
            self.kmods.add(('exfat', ()))
        elif self.filesystem in ('f2fs',):
            self.execs.add(('fsck.f2fs', None))
            self.kmods.add(('f2fs', ()))
        elif self.filesystem in ('zfs',):
            self.execs.add(('fsck.zfs', None))
            self.kmods.add(('zfs', ()))
        self.add_dep(self.source)

    def __str__(self) -> str:
        return self.mountpoint

    def __eq__(self, other: object) -> bool:
        return isinstance(other, type(self)) and super().__eq__(other) \
            and self.source == other.source \
            and self.mountpoint == other.mountpoint \
            and self.filesystem == other.filesystem \
            and self.options == other.options

    def load(self, out: IO[str]) -> None:
        fsck_exec = f'fsck -t {quote(self.filesystem)}' \
            if self.filesystem != 'zfs' else 'fsck.zfs'
        fsck = (
            f'mount_fsck {fsck_exec} {self.source.path()} || die ',
            quote(f'Failed to check filesystem {self}'), '\n',
        ) if self.source.path() != 'none' else ()
        mkdir = self.mkdir(self.mountpoint) \
            if os.path.dirname(self.mountpoint) == '/mnt' else ()

        self._pre_load(out)
        out.writelines((
            f"info 'Mounting filesystem {self}'\n",
            *fsck,
            *mkdir,
            f"mount -t {quote(self.filesystem)} -o {quote(self.options)} ",
            f"{self.source.path()} {quote(self.mountpoint)} || die ",
            quote(f'Failed to mount filesystem {self}'), '\n',
            "\n",
        ))
        self._post_load(out)

    def unload(self, out: IO[str]) -> None:
        self._pre_unload(out)
        out.writelines((
            f"info 'Unmounting filesystem {self}'\n",
            f"umount {quote(self.mountpoint)} || die ",
            quote(f'Failed to unmount filesystem {self}'), '\n',
            "\n",
        ))
        self._post_unload(out)

    def path(self) -> str:
        return quote(self.mountpoint)


class MdData(Data):
    """MD RAID

    :param sources: :class:`Data` to use as sources (e.g. /dev/sda1 and
        /dev/sdb1; or UUID=foo), they will be set as hard dependencies
    :param name: Name for the MD device
    :raises ValueError: No :class:`Data` source
    """
    sources: Tuple[Data, ...]
    name: str

    def __init__(self, sources: Iterable[Data], name: str):
        super().__init__()
        self.execs.add(('mdadm', None))
        self.sources = tuple(sources)
        self.name = name
        if not self.sources:
            raise ValueError(f"{self} has no source defined")
        for source in self.sources:
            self.add_dep(source)

    def __str__(self) -> str:
        return self.name

    def __eq__(self, other: object) -> bool:
        return isinstance(other, type(self)) and super().__eq__(other) \
            and self.sources == other.sources and self.name == other.name

    def load(self, out: IO[str]) -> None:
        # Get the string containing all sources to use
        sources: Set[str] = set()
        for source in self.sources:
            if isinstance(source, UuidData):
                sources.add(f"--uuid {quote(source.uuid)} ")
            else:
                sources.add(f"{source.path()} ")

        self._pre_load(out)
        out.writelines((
            f"info 'Assembling MD RAID {self}'\n",
            "MDADM_NO_UDEV=1 ",
            "mdadm --assemble ", *sources, f"{quote(self.name)} || die ",
            quote(f'Failed to assemble MD RAID {self}'), '\n',
            "\n",
        ))
        self._post_load(out)

    def unload(self, out: IO[str]) -> None:
        self._pre_unload(out)
        out.writelines((
            f"info 'Stopping MD RAID {self}'\n",
            "MDADM_NO_UDEV=1 ",
            f"mdadm --stop {quote(self.name)} || die ",
            quote(f'Failed to stop MD RAID {self}'), '\n',
            "\n",
        ))
        self._post_unload(out)

    def path(self) -> str:
        return quote('/dev/md/' + self.name)


class CloneData(Data):
    """Clone a :class:`Data` to another

    :param source: :class:`Data` to use as source,
        it will be set as a load dependency
    :param dest: :class:`Data` to use as destination,
        it will be set as a hard dependency
    """
    source: Data
    dest: Data

    def __init__(self, source: Data, dest: Data):
        super().__init__()
        self.busybox |= {'cp'}
        self.source = source
        self.dest = dest
        self.add_load_dep(self.source)
        self.add_dep(self.dest)

    def __str__(self) -> str:
        return f"{self.source} to {self.dest}"

    def __eq__(self, other: object) -> bool:
        return isinstance(other, type(self)) and super().__eq__(other) \
            and self.source == other.source and self.dest == other.dest

    def load(self, out: IO[str]) -> None:
        self._pre_load(out)
        out.writelines((
            f"info 'Cloning {self}'\n",
            f"cp -aT {self.source.path()} {self.dest.path()} || die ",
            quote(f'Failed to clone {self}'), '\n',
            "\n",
        ))
        self._post_load(out)

    def path(self) -> str:
        return self.dest.path()


class ZFSPoolData(Data):
    """ ZFS pool

    :param pool: Pool name
    :param cache: :class:`Data` containing a ZFS cache file,
        it will be set as a load dependency
    """
    pool: str
    cache: Optional[Data]

    def __init__(self, pool: str, cache: Optional[Data]):
        super().__init__()
        self.pool = pool
        self.cache = cache

        if self.cache is not None:
            self.add_load_dep(self.cache)
        self.execs.add(('zpool', None))
        self.kmods.add(('zfs', ()))

    def __str__(self) -> str:
        return f'ZFS pool {self.pool}'

    def __eq__(self, other: object) -> bool:
        return isinstance(other, type(self)) and super().__eq__(other) \
            and self.pool == other.pool and self.cache == other.cache

    def load(self, out: IO[str]) -> None:
        self._pre_load(out)
        cache = f'-c {self.cache.path()} ' if self.cache is not None else ''
        out.writelines((
            'info ', quote(f'Importing {self}'), '\n',
            'zpool import -N ', cache, quote(self.pool), ' || die ',
            quote(f'Failed to import {self}'), '\n',
            '\n',
        ))
        self._post_load(out)

    def unload(self, out: IO[str]) -> None:
        self._pre_unload(out)
        out.writelines((
            'info ', quote(f'Importing {self}'), '\n',
            'zpool export ', quote(self.pool), ' || die',
            quote(f'Failed to export {self}'), '\n',
            '\n',
        ))
        self._post_unload(out)

    def path(self) -> str:
        return quote(self.pool)


class ZFSCryptData(Data):
    """ZFS encrypted dataset

    :param pool: :class:`ZFSPoolData` containing the encrypted dataset,
        it will be set as a hard dependency
    :param dataset: Dataset name
    :param key: :class:`Data` to use as key file,
        it will be set as a load dependency
    """
    pool: ZFSPoolData
    dataset: str
    key: Optional[Data]

    def __init__(self, pool: Data, dataset: str,
                 key: Optional[Data] = None):
        super().__init__()
        if not isinstance(pool, ZFSPoolData):
            raise TypeError(f"{self.pool} is not a {ZFSPoolData}")
        self.pool = pool
        self.dataset = dataset
        self.key = key

        if self.pool.pool != dataset.split('/')[0]:
            raise Exception(f"{self} is not on pool {self.pool}")
        self.add_dep(self.pool)
        if self.key is not None:
            self.add_load_dep(self.key)

        self.execs.add(('zfs', None))

    def __str__(self) -> str:
        return f'ZFS encrypted dataset {self.dataset}'

    def __eq__(self, other: object) -> bool:
        return isinstance(other, type(self)) and super().__eq__(other) \
            and self.pool == other.pool and self.dataset == other.dataset \
            and self.key == other.key

    def load(self, out: IO[str]) -> None:
        self._pre_load(out)
        key = f'-L {self.key.path()} ' if self.key is not None else ''
        out.writelines((
            'info ', quote(f'Unlocking {self}'), '\n',
            f'zfs load-key -r {key}{quote(self.dataset)} 1>&2 || die ',
            quote(f'Failed to unlock {self}'), '\n',
            '\n',
        ))
        self._post_load(out)

    def unload(self, out: IO[str]) -> None:
        self._pre_unload(out)
        out.writelines((
            'info ', quote(f'Locking {self}'), '\n',
            f'zfs unload-key -r {quote(self.dataset)} || die ',
            quote(f'Failed to lock {self}'), '\n',
            '\n',
        ))
        self._post_unload(out)

    def path(self) -> str:
        return quote(self.dataset)


class Network(Data):
    """Networking configuration

    :param device: MAC address of the device
    :param ip: IP address (None for DHCP)
    :param mask: IP mask (defaults to classful or DHCP)
    :param gateway: default route IP (optional)
    """
    device: str
    ip: Optional[str]
    mask: Optional[str]
    gateway: Optional[str]

    @staticmethod
    def classful_mask(ip: str) -> str:
        first = int(ip.split('.')[0])
        if first < 128:
            return '255.0.0.0'
        elif first < 192:
            return '255.255.0.0'
        elif first < 224:
            return '255.255.255.0'
        else:
            raise ValueError(f"No classful network mask for {ip}")

    @staticmethod
    def __fun_find_iface(out: IO[str]) -> None:
        """"Define the ``find_iface`` function

        This function takes one MAC address and outputs the corresponding
        network interface name (e.g. ``eth0``).

        Return value: 0 on success, 1 on failure.
        """

        out.writelines((
            'find_iface()\n',
            '{\n',
            '\tfor k in /sys/class/net/*; do\n',
            '\t\tif ! grep -q "${1}" "${k}/address" 1>/dev/null 2>&1; ',
            'then continue; fi\n',
            '\t\techo "$(basename -- "${k}")"\n',
            '\t\treturn 0\n',
            '\tdone\n',
            '\treturn 1\n'
            '}\n',
            '\n',
        ))

    @classmethod
    def initialize(cls, out: IO[str]) -> None:
        super().initialize(out)
        Network.__fun_find_iface(out)

    def __init__(
        self, device: str, ip: Optional[str] = None,
        mask: Optional[str] = None, gateway: Optional[str] = None
    ):
        super().__init__()

        self.device = device
        if mask is None and ip is not None:
            mask = Network.classful_mask(ip)
        self.ip = ip
        self.mask = mask
        self.gateway = gateway

        self.busybox |= {'ip', 'udhcpc'}
        self.files |= {
            ('/usr/share/udhcpc/default.script', '/etc/udhcpc.script'),
        }

    def __str__(self) -> str:
        return f'network interface {self.device}'

    def __eq__(self, other: object) -> bool:
        return isinstance(other, type(self)) and super().__eq__(other) \
            and self.ip == other.ip and self.mask == other.mask \
            and self.gateway == other.gateway and self.device == other.device

    def load(self, out: IO[str]) -> None:
        device = quote(self.device)
        ip = quote(self.ip if self.ip is not None else '')
        mask = quote(self.mask if self.mask is not None else '')
        gateway = quote(self.gateway if self.gateway is not None else '')
        iface = '"${iface}"'
        iface_full = quote(f'{self.device} (') + iface + quote(')')

        static_ip = (
            f'ip addr add {ip}/{mask} dev {iface} || die ',
            quote(f'Failed to add {self.ip} to '), iface_full, '\n',
        )
        dhcp_ip = (
            f'udhcpc -nqfS -s /etc/udhcpc.script -i {iface} || die ',
            quote('DHCP failed on '), iface_full, '\n',
        )
        gw_route = (
            f'ip route add default via {gateway} dev {iface} || die ',
            quote(f'Failed to set gateway {self.gateway} on '),
            iface_full, '\n',
        )

        self._pre_load(out)
        out.writelines((
            'info ', quote(f'Raising {self}'), '\n',
            f'iface="$(find_iface {device})" || die ',
            quote(f'Failed to find network interface {self.device}'), '\n',
            f'ip link set {iface} up || die ',
            quote('Failed to raise network interface '), iface_full, '\n',
            *(static_ip if self.ip is not None else dhcp_ip),
            *(gw_route if self.gateway is not None else ()),
            '\n',
        ))
        self._post_load(out)

    def unload(self, out: IO[str]) -> None:
        device = quote(self.device)
        iface = '"${iface}"'
        iface_full = quote(f'{self.device} (') + iface + quote(')')

        self._pre_unload(out)
        out.writelines((
            'info ', quote(f'Shutting down {self}'), '\n',
            f'iface="$(find_iface {device})" || die ',
            quote(f'Failed to find network interface {self.device}'), '\n',
            f'ip link set {iface} down || die ',
            quote('Failed to shutdown network interface '), iface_full, '\n',
            '\n',
        ))
        self._post_unload(out)


class ISCSI(Data):
    """iSCSI target

    :param initiator: Initiator name
    :param target: iSCSI target
    :param portal_group: Target portal group tag
    :param address: iSCSI server address
    :param port: iSCSI server port
    :param username: Authentication username
    :param password: Authentication password
    :param username_in: Incoming authentication username
    :param password_in: Incoming authentication password
    """
    initiator: str
    target: str
    portal_group: int
    address: str
    port: int
    username: Optional[str]
    password: Optional[str]
    username_in: Optional[str]
    password_in: Optional[str]

    def __init__(
        self,
        initiator: str,
        target: str,
        portal_group: int,
        address: str,
        port: int = 3260,
        username: Optional[str] = None,
        password: Optional[str] = None,
        username_in: Optional[str] = None,
        password_in: Optional[str] = None,
    ):
        super().__init__()

        self.initiator = initiator
        self.target = target
        self.portal_group = portal_group
        self.address = address
        self.port = port
        self.username = username
        self.password = password
        self.username_in = username_in
        self.password_in = password_in

        if (self.username is None) != (self.password is None):
            raise ValueError("Both username and password must be set")
        if (self.username_in is None) != (self.password_in is None):
            raise ValueError("Both username_in and password_in must be set")

        self.execs |= {('iscsistart', None)}

    def __str__(self) -> str:
        return f'iSCSI target {self.target}'

    def __eq__(self, other: object) -> bool:
        return isinstance(other, type(self)) and super().__eq__(other) \
            and self.initiator == other.initiator \
            and self.target == other.target \
            and self.portal_group == other.portal_group \
            and self.address == other.address \
            and self.port == other.port \
            and self.username == other.username \
            and self.password == other.password \
            and self.username_in == other.username_in \
            and self.password_in == other.password_in

    def load(self, out: IO[str]) -> None:
        auth = (
            ' -u ', quote(
                self.username if self.username is not None else ''
            ),
            ' -w ', quote(
                self.password if self.password is not None else ''
            ),
        )
        auth_in = (
            ' -U ', quote(
                self.username_in if self.username_in is not None else ''
            ),
            ' -W ', quote(
                self.password_in if self.password_in is not None else ''
            ),
        )

        self._pre_load(out)
        out.writelines((
            'info ', quote(f'Loading {self}'), '\n',
            'iscsistart',
            ' -i ', quote(self.initiator),
            ' -t ', quote(self.target),
            ' -g ', str(self.portal_group),
            ' -a ', quote(self.address),
            ' -p ', str(self.port),
            *(auth if self.username is not None else ()),
            *(auth_in if self.username_in is not None else ()),
            ' || die ', quote(f'Failed to load {self}'), '\n',
            '\n',
        ))
        self._post_load(out)

    def unload(self, out: IO[str]) -> None:
        self._pre_unload(out)
        self._post_unload(out)
