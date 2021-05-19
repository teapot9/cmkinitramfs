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

        This functions calls ``fsck`` with all the arguments it was called
        with. It checks the return code of ``fsck`` and :

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
            '\tFSTAB_FILE=/dev/null fsck "$@"\n',
            '\tfsck_ret=$?\n'
            '\t[ "${fsck_ret}" -eq 0 ] && return 0\n',
        ))
        for err_code in fsck_err:
            err_call, err_str = fsck_err[err_code]
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
        fsck = (
            f'mount_fsck -t {quote(self.filesystem)} ',
            f'{self.source.path()} || die ',
            quote(f'Failed to check filesystem {self}'), '\n',
        ) if self.source.path() != 'none' else ()
        mkdir = (
            f"[ -d {quote(self.mountpoint)} ] || ",
            f"mkdir {quote(self.mountpoint)} || err ",
            quote(f'Failed to create directory {self}'), '\n',
        ) if os.path.dirname(self.mountpoint) == '/mnt' else ()

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
