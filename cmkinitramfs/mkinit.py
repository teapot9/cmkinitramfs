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

import itertools
import locale
import os.path
from enum import Enum, auto
from shlex import quote
from typing import FrozenSet, Iterable, Iterator, IO, Optional, Set, Tuple


class Breakpoint(Enum):
    """Breakpoint in the boot process

    Breakpoints can be enabled by adding rd.break to the kernel command-line
    (e.g. ``./kernel.img foo rd.break=init``).
    Setting ``rd.break=foo,bar`` will enable both ``foo`` and ``bar``.
    Environment variables can also be set to enable them
    (e.g. ``./kernel.img foo RD_BREAK_EARLY=true``).
    """
    #: Early break: break before any action, including command-line parsing.
    #: Can be set with the ``RD_BREAK_EARLY`` environment variable.
    EARLY = auto()
    #: ``init``: Break after initramfs initialization.
    #: Can also be set with the ``RD_BREAK_INIT`` environment variable.
    INIT = auto()
    #: ``module``: Break after loading kernel modules.
    #: Can also be set with the ``RD_BREAK_MODULE`` environment variable.
    MODULE = auto()
    #: ``rootfs``: Break after mounting the root filesystem.
    #: Can also be set with the ``RD_BREAK_ROOTFS`` environment variable.
    ROOTFS = auto()
    #: ``mount``: Break after mounting all filesystems.
    #: Can also be set with the ``RD_BREAK_MOUNT`` environment variable.
    MOUNT = auto()


def _fun_rescue_shell(out: IO[str]) -> None:
    """Define the rescue_shell function

    ``rescue_shell`` takes one argument and drop the user to ``/bin/sh``,
    the argument is the error to print to the user.

    This function *should not* be called from a subshell.

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


def _fun_panic(out: IO[str]) -> None:
    """Define the panic function

    ``panic`` causes a kernel panic by exiting ``/init``.
    It takes one argument: the error message.

    This function *should not* be called from a subshell.

    :param out: Stream to write into
    """
    out.writelines((
        "panic()\n",
        "{\n",
        "\tprintk \"$1\"\n",
        "\techo 'Terminating init'\n",
        "\tsync\n",
        "\texit\n",
        "}\n\n",
    ))


def _fun_die(out: IO[str]) -> None:
    """Define the die function

    ``die`` will either start a rescue shell or cause a kernel panic,
    wether ``RD_PANIC`` is set or not.
    It takes one argument: the error message passed to ``panic``
    or ``rescue_shell``.

    This function *should not* be called from a subshell.

    :param out: Stream to write into
    """
    out.writelines((
        "die()\n",
        "{\n",
        "\t[ -n \"${RD_PANIC+x}\" ] && panic \"$1\" || rescue_shell \"$1\"\n",
        "}\n\n",
    ))


def _die(message: str, newline: bool = True) -> str:
    """Stop the boot process with an error

    This is a helper calling ``die``.

    :param message: Error message to print
    :param newline: Include a newline at the end of the function call
    :return: String stopping the boot proces, the error message will be
        single quoted and escaped
    """
    end = '\n' if newline else ''
    return f"die {quote(f'FATAL: {message}')}{end}"


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
    _fun_panic(out)
    _fun_die(out)
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

    Note: the command line is parsed up to "--", arguments after this
    are passed through to the final init process.

    Parsed parameters:

     - ``rd.break={init|rootfs|mount}``: Stops the boot process,
       defaults to ``rootfs``. See :class:`Breakpoint`.
     - ``rd.debug``: Enable debugging mode (with ``set -x``).
     - ``rd.panic``: On fatal error: cause a kernel panic rather than
       dropping into a shell.

    :param out: Stream to write into
    """
    out.writelines((
        "echo 'Parsing command-line'\n",
        "for cmdline in $(cat /proc/cmdline); do\n",
        "\tcase \"${cmdline}\" in\n",
        "\t--) break ;;\n",
        "\trd.break) RD_BREAK_ROOTFS=true ;;\n",
        "\trd.break=*)\n",
        "\t\tOLDIFS=\"${IFS}\"\n",
        "\t\tIFS=','\n",
        "\t\tfor bpoint in ${cmdline#*=}; do\n",
        "\t\t\tcase \"${bpoint}\" in\n",
        "\t\t\tinit) RD_BREAK_INIT=true ;;\n",
        "\t\t\trootfs) RD_BREAK_ROOTFS=true ;;\n",
        "\t\t\tmount) RD_BREAK_MOUNT=true ;;\n",
        "\t\t\t*) printk \"ERROR: Unknown breakpoint ${bpoint}\" ;;\n",
        "\t\t\tesac\n",
        "\t\tdone\n",
        "\t\tIFS=\"${OLDIFS}\"\n",
        "\t\t;;\n",
        "\trd.debug) RD_DEBUG=true ;;\n",
        "\trd.panic) RD_PANIC=true ;;\n",
        "\tesac\n",
        "done\n",
        "\n",
        "[ -n \"${RD_DEBUG+x}\" ] && PS4='+ $0:$LINENO: ' && set -x\n",
        "\n",
    ))


def do_keymap(out: IO[str], keymap_file: str, unicode: bool = True) -> None:
    """Load a keymap

    :param out: Stream to write into
    :param keymap_file: Absolute path of the file to load
    :param unicode: Set the keyboard in unicode mode (rather than ASCII)
    """
    out.writelines((
        "echo 'Loading keymap'\n",
        f"[ -f {quote(keymap_file)} ] || ",
        _die(f'Failed to load keymap, file {keymap_file} not found'),
        f"kbd_mode {'-u' if unicode else '-a'} || ",
        _die('Failed to set keyboard mode to '
             f"{'unicode' if unicode else 'ASCII'}"),
        f"loadkmap <{quote(keymap_file)} || ",
        _die(f'Failed to load keymap {keymap_file}'),
        "\n",
    ))


def do_module(out: IO[str], module: str, *args: str) -> None:
    """Load a kernel module

    :param out: Stream to write into
    :param module: Name of the module to load
    :param args: Arguments for the module (passed to ``modprobe``)
    """
    quoted_args = (f'{quote(arg)} ' for arg in args)

    out.writelines((
        f"echo 'Loading kernel module {module}'\n",
        f"modprobe {quote(module)} ", *quoted_args, '|| ',
        _die(f'Failed to load module {module}'),
        '\n',
    ))


def do_break(out: IO[str], breakpoint_: Breakpoint) -> None:
    """Drop into a shell if rd.break is set

    :param out: Stream to write into
    :param breakpoint_: Which breakpoint to check
    """
    if breakpoint_ is Breakpoint.EARLY:
        breakname = 'RD_BREAK_EARLY'
    elif breakpoint_ is Breakpoint.INIT:
        breakname = 'RD_BREAK_INIT'
    elif breakpoint_ is Breakpoint.MODULE:
        breakname = 'RD_BREAK_MODULE'
    elif breakpoint_ is Breakpoint.ROOTFS:
        breakname = 'RD_BREAK_ROOTFS'
    elif breakpoint_ is Breakpoint.MOUNT:
        breakname = 'RD_BREAK_MOUNT'
    else:
        raise ValueError(f"Unknown breakpoint: {breakpoint_}")

    out.writelines((
        "[ -n \"${", breakname, "+x}\" ] && rescue_shell ",
        quote(f"Reached {breakpoint_}"),
        "\n\n",
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
        Each file is a tuple in the format (``src``, ``dest``),
        where ``src`` is the source file on the current system,
        and ``dest`` is the destination in the initramfs
        (relative to its root directory).
        If ``dest`` is :data:`None`, then ``src`` is used.
    :param execs: Executables directly needed in the initramfs.
        Same format as :attr:`files`.
    :param libs: Libraries directly needed in the initramfs.
        Same format as :attr:`files`.
    :param _need: Loading and runtime dependencies
    :param _lneed: Loading only dependencies
    :param _needed_by: Reverse dependencies
    :param _is_final: The :class:`Data` should not be unloaded
    :param _is_loaded: The :class:`Data` is currently loaded
    """
    files: Set[Tuple[str, Optional[str]]]
    execs: Set[Tuple[str, Optional[str]]]
    libs: Set[Tuple[str, Optional[str]]]
    _need: Set[Data]
    _lneed: Set[Data]
    _needed_by: Set[Data]
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
        self._need = set()
        self._lneed = set()
        self._needed_by = set()
        self._is_final = False
        self._is_loaded = False

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
        self._need.add(dep)
        dep._needed_by.add(self)

    def add_load_dep(self, dep: Data) -> None:
        """Add a :class:`Data` object to the loading dependencies"""
        self._lneed.add(dep)
        dep._needed_by.add(self)

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
        for k in self._need | self._lneed:
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
            1: "Filesystem errors corrected",
            2: "System should be rebooted",
            4: "Filesystem errors left uncorrected",
            8: "Operational error",
            16: "Usage or syntax error",
            32: "Checking canceled by user request",
            128: "Shared-library error",
        }

        out.writelines((
            'mount_fsck()\n',
            '{\n',
            '\tfsck "$@"\n',
            '\tfsck_ret=$?\n'
            '\t[ "${fsck_ret}" -eq 0 ] && return 0\n',
        ))
        for err_code, err_str in fsck_err.items():
            out.writelines((
                f'\t[ "$((fsck_ret & {err_code}))" -eq {err_code} ] && ',
                'printk ', quote(f"fsck: {err_str}"), '\n',
            ))
        # 252 = 4 | 8 | 16 | 32 | 64 | 128
        out.writelines((
            '\t[ "$((fsck_ret & 252))" -ne 0 ] && return 1\n',
            '\tif [ "$((fsck_ret & 2))" -eq 2 ]; then ',
            'printk \'Rebooting...\'; reboot -f; fi\n',
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
            f'mount_fsck -t {quote(self.filesystem)} {self.source.path()} || ',
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
        sources: Set[str] = set()
        for source in self.sources:
            if isinstance(source, UuidData):
                sources.add(f"--uuid {quote(source.uuid)} ")
            else:
                sources.add(f"{source.path()} ")

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


def mkinit(
        out: IO[str],
        root: Data,
        mounts: Optional[Iterable[Data]] = None,
        keymap: Optional[str] = None,
        init: Optional[str] = None,
        modules: Iterable[Tuple[str, Iterable[str]]] = (),
        ) -> None:  # noqa: E123
    """Create the init script

    :param out: Stream to write into
    :param root: :class:`Data` to use as rootfs
    :param mounts: :class:`Data` needed in addition of rootfs
    :param keymap: Path of the keymap to load, :data:`None` means no keymap
    :param init: Init script to use, defaults to ``/sbin/init``
    :param modules: Kernel modules to be load in the initramfs:
        ``(module, (arg, ...))``. ``module`` is the module name string,
        and ``(arg, ...)``` is the iterable with the module parameters.
    """
    if mounts is None:
        mounts = set()
    if init is None:
        init = '/sbin/init'

    datatypes = set()
    for data in itertools.chain((root,), mounts):
        datatypes.add(type(data))
        for dep in data.iter_all_deps():
            datatypes.add(type(dep))

    do_header(out)
    do_break(out, Breakpoint.EARLY)
    do_init(out)
    for datatype in datatypes:
        datatype.initialize(out)
    do_cmdline(out)
    if keymap is not None:
        do_keymap(out, keymap,
                  unicode=(locale.getdefaultlocale()[1] == 'UTF-8'))
    do_break(out, Breakpoint.INIT)
    for mod_name, mod_args in modules:
        do_module(out, mod_name, *mod_args)
    do_break(out, Breakpoint.MODULE)
    root.load(out)
    do_break(out, Breakpoint.ROOTFS)
    for mount in mounts:
        mount.load(out)
    do_break(out, Breakpoint.MOUNT)
    do_switch_root(out, init, root)
