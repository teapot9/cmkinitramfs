"""Library providing functions and classes to build an /init script

do_xxx() functions will return a string for the xxx action. This string is to
be written into the /init script.

_fun_xxx() functions return a string to declare the xxx function available
from within the /init script.

The Data class defines an abstract object containing data, it has multiple
herited classes for multiple source of data.
"""

import os.path
from shlex import quote
from typing import List, Optional, Set, Tuple

from cmkinitramfs.util import read_config


def _fun_rescue_shell() -> str:
    """Rescue shell
    This function takes one argument and drop the user to /bin/sh,
    the argument is the error string for the user.
    """
    return (
        "rescue_shell()\n"
        "{\n"
        "\tprintk \"$1\"\n"
        "\techo 'Dropping you into a shell'\n"
        "\texec '/bin/sh'\n"
        "}\n"
    )


def _fun_printk() -> str:
    """Outputs a string to kernel log and to stderr"""
    return (
        "printk()\n"
        "{\n"
        "\techo \"initramfs: $1\" 1>/dev/kmsg\n"
        "\techo \"$1\" 1>&2\n"
        "}\n"
    )


def _die(message: str) -> str:
    """Returns a string stopping the boot process
    The string will be single quoted and escaped.
    This function will load a rescue shell with an error message,
    this is an abstraction for the rescue_shell function
    """
    return f"rescue_shell {quote(f'FATAL: {message}')}"


def do_header(home: str = "/root", path: str = "/bin:/sbin") -> str:
    """Create the /init header
    This will return:
      - The shebang /bin/sh
      - Configure HOME variable, defaults to /root
      - Configure PATH variable, defaults to /bin:/sbin
      - Declare functions
    """
    return (
        "#!/bin/sh\n"
        "\n"
        f"HOME={quote(home)}\n"
        "export HOME\n"
        f"PATH={quote(path)}\n"
        "export PATH\n"
        "\n"
        f"{_fun_rescue_shell()}\n"
        f"{_fun_printk()}\n"
        "echo 'INITRAMFS: Start'\n"
        "\n"
    )


def do_init() -> str:
    """Initialize the init environment
    This action will:
      - Check current PID is 1
      - Mount /proc, /sys, /dev
      - Set kernel log level to 3
    """
    return (
        "echo 'Initialization'\n"
        "test $$ -eq 1 || "
        f"{_die('init expects to be run as PID 1')}\n"
        "mount -t proc none /proc || "
        f"{_die('Failed to mount /proc')}\n"
        "mount -t sysfs none /sys || "
        f"{_die('Failed to mount /sys')}\n"
        "mount -t devtmpfs none /dev || "
        f"{_die('Failed to mount /dev')}\n"
        "echo 3 1>'/proc/sys/kernel/printk'\n"
        "\n"
    )


def do_cmdline() -> str:
    """Parse the kernel command line for known parameters
    Parsed parameters are:
      - rescue_shell: Immediately starts a rescue shell
      - maintenance: Starts a rescue shell after mounting rootfs
    """
    return (
        "for cmdline in $(cat /proc/cmdline); do\n"
        "\tcase \"${cmdline}\" in\n"
        "\t\trescue_shell) rescue_shell 'Manual rescue shell';;\n"
        "\t\tmaintenance) MAINTENANCE=true;;\n"
        "\tesac\n"
        "done\n"
        "\n"
    )


def do_keymap(keymap_file: str) -> str:
    """Load the keymap
    keymap_file -- String: absolute path to the keymap file
    within the initramfs
    """
    return (
        "echo 'Loading keymap'\n"
        f"[ -f {quote(keymap_file)} ] || "
        f"{_die(f'Failed to load keymap, file {keymap_file} not found')}\n"
        f"loadkmap <{quote(keymap_file)} || "
        f"{_die(f'Failed to load keymap {keymap_file}')}\n"
        "\n"
    )


def do_maintenance() -> str:
    """Check for maintenance
    If the MAINTENANCE variable is set, load a rescue shell
    """
    return (
        "[ -n \"${MAINTENANCE}\" ] && "
        "rescue_shell 'Going into maintenance mode'\n"
        "\n"
    )


def do_switch_root(init: str, newroot: 'Data') -> str:
    """Cleanup and switch root
    This action will:
      - Set kernel log level back to default
      - Dismount /dev, /sys, /proc
      - Switch root
    init -- String: init process to execute from new root
    newroot -- Data: source to use as new root
    """
    return (
        f"printk 'Run {init} as init process'\n"
        "verb=\"$(awk '{ print $4 }' /proc/sys/kernel/printk)\"\n"
        'echo "${verb}" >/proc/sys/kernel/printk\n'
        f"umount /dev || {_die('Failed to unmount /dev')}\n"
        f"umount /proc || {_die('Failed to unmount /proc')}\n"
        f"umount /sys || {_die('Failed to unmount /sys')}\n"
        "echo 'INITRAMFS: End'\n"
        f"exec switch_root {newroot.path()} {quote(init)}\n"
    )


class Data:
    """Data class for any object representing Data on the system
    This is an abstract class representing an object containing data.
    It has two main methods: load() and unload(), and several related
    methods.
    The method set_final() declare the object as required for the final
    boot environment (e.g. rootfs).

    Private attributes:
    files -- Set of strings: files needed in the initramfs
    execs -- Set of strings: executables needed in the initramfs
    libs -- Set of strings: libraries needed in the initramfs
    _need -- List of Data objects needed
    _lneed -- List of Data objects needed for load (those objects
      can be unloaded once the current object is loaded)
    _needed_by -- List of Data objects depending on this object
    _is_final -- bool: Does the data is needed by the final boot environment?
    _is_loaded -- bool: Data is currently loaded
    """

    def __init__(self) -> None:
        self.files: Set[Tuple[str, Optional[str]]] = set()
        self.execs: Set[Tuple[str, Optional[str]]] = set()
        self.libs: Set[Tuple[str, Optional[str]]] = set()
        self._need: List['Data'] = []
        self._lneed: List['Data'] = []
        self._is_final = False
        self._is_loaded = False
        self._needed_by: List['Data'] = []

    def deps_files(self) -> Set[Tuple[str, Optional[str]]]:
        "Recursivelly get a set of files needed in the initramfs"
        return self.files.union(
            *(k.deps_files() for k in self._need + self._lneed)
        )

    def deps_execs(self) -> Set[Tuple[str, Optional[str]]]:
        "Recursivelly get a set of executables needed in the initramfs"
        return self.execs.union(
            *(k.deps_execs() for k in self._need + self._lneed)
        )

    def deps_libs(self) -> Set[Tuple[str, Optional[str]]]:
        "Recursivelly get a set of libraries needed in the initramfs"
        return self.libs.union(
            *(k.deps_libs() for k in self._need + self._lneed)
        )

    def is_final(self) -> bool:
        """Returns a boolean indicating if the data is final"""
        return self._is_final

    def set_final(self) -> None:
        """This function set the data object as final
        This means the data is required by the final boot environment
        and should never be unloaded (as it would be pointless).
        This will also flag its hard dependencies as final.
        """
        self._is_final = True
        for k in self._need:
            k.set_final()

    def add_dep(self, dep: 'Data') -> None:
        """Add a Data object to the hard dependencies list"""
        self._need.append(dep)
        dep._needed_by.append(self)

    def add_load_dep(self, dep: 'Data') -> None:
        """Add a Data object to the loading dependencies list"""
        self._lneed.append(dep)
        dep._needed_by.append(self)

    def pre_load(self) -> str:
        """This function does the preparation for loading the Data
        It loads all the needed dependencies to the system.
        It should be called before the actual loading of the Data.
        This method *should not* be called if the Data is already loaded.
        Returns a string containing the pre-loading script.
        """
        code = ""
        if self._is_loaded:
            raise DataError(f"{self} is already loaded")
        self._is_loaded = True
        # Load dependencies
        for k in self._need + self._lneed:
            if not k._is_loaded:
                code += k.load()
        return code

    def post_load(self) -> str:
        """This function does the post loading cleanup
        If the object is a loading dependency only,
        it will load everything needing it in order to be unloaded.
        It should be called after the actual loading of the Data.
        Returns a string containing the post-loading script.
        """
        code = ""
        # If not final, load data needing self, this will allow an
        # unloading as soon as possible
        if not self._is_final:
            for k in self._needed_by:
                if not k._is_loaded:
                    code += k.load()
        # Unload data not needed anymore
        for k in self._lneed:
            k._needed_by.remove(self)
            if not k._needed_by:
                code += k.unload()
        return code

    def load(self) -> str:
        """This function is the actual loading of the Data
        It should be redefined by the herited classes,
        this definition is a no-op only loading dependencies.
        Before loading, this function should:
          - Load the dependencies with pre_load()
        After loading, this function should:
          - Unload unnecessary dependencies with post_load()
        This method *should not* be called if the data is already loaded.
        Returns a string containing the loading script.
        """
        return self.pre_load() + self.post_load()

    def pre_unload(self) -> str:
        """This function does the pre unloading sanity checks
        It should be called before the actual unloading of the data.
        Returns a string containing the pre-unloading script.
        """
        code = ""
        if not self._is_loaded:
            raise DataError(f"{self} is not loaded")
        if self._is_final or self._needed_by:
            raise DataError(f"{self} is still needed or not temporary")
        return code

    def post_unload(self) -> str:
        """This function does the post unloading cleanup
        It removes itself from the _needed_by list of all its dependencies
        and check if the dependency can be unloaded.
        This method should be called after the unloading of the Data.
        This *should not* be called if the data is not loaded.
        Returns a string containing the post-unloading script.
        """
        code = ""
        for k in self._need:
            k._needed_by.remove(self)
            if not k._needed_by:
                code += k.unload()
        self._is_loaded = False
        return code

    def unload(self) -> str:
        """This function does the unloading of the Data
        It should be redefined by the herited classes,
        this definition is a no-op only unloading unneeded dependencies.
        Before unloading, this function should:
          - Check for any dependency error, with pre_unload()
        After unloading, this function should:
          - Unload all unneeded dependencies, with post_unload()
        Returns a string containing the unloading script.
        """
        return self.pre_unload() + self.post_unload()

    def __str__(self) -> str:
        """Get the name of the data
        This string may be quoted with simple quotes in the script.
        This **has** to be implemented by subclasses.
        """
        raise NotImplementedError()

    def path(self) -> str:
        """Get the path of this data
        This function provides a string allowing access to data from /init,
        this string can be a path or a command in a subshell (e.g.
        "$(findfs UUID=foobar)").
        This string should be ready to be used in the script without
        being quoted nor escaped.
        This **has** to be implemented by subclasses.
        """
        raise NotImplementedError()


class DataError(Exception):
    """Error in the Data object"""


class PathData(Data):
    """PathData class: Absolute path

    Attributes:
    filepath -- String: path of the data
    """

    def __init__(self, path: str):
        super().__init__()
        self.filepath = path

    def __str__(self) -> str:
        return self.filepath

    def path(self) -> str:
        return quote(self.filepath)


class UuidData(Data):
    """UuidData class: UUID for device

    Attributes:
    uuid -- String: UUID of the data
    """

    def __init__(self, uuid: str):
        super().__init__()
        self.uuid = uuid

    def __str__(self) -> str:
        return "UUID=" + self.uuid

    def path(self) -> str:
        return '"$(findfs ' + quote('UUID=' + self.uuid) + ')"'


class LuksData(Data):
    """LuksData class: LUKS encrypted partition

    Attributes:
    source -- Data to unlock (crypto_LUKS volume)
    name -- String: name used by LUKS for the device
    key -- Data to use as key, defaults to None: no key file
    header -- Data to use as header, defaults to None: not needed
    discard -- Enable discards
    """

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

    def load(self) -> str:
        header = f'--header {self.header.path()} ' if self.header else ''
        key_file = f'--key-file {self.key.path()} ' if self.key else ''
        discard = '--allow-discards ' if self.discard else ''
        return (
            f"{self.pre_load()}"
            f"echo 'Unlocking LUKS device {self}'\n"
            f"cryptsetup {header}{key_file}{discard}"
            f"open {self.source.path()} {quote(self.name)} || "
            f"{_die(f'Failed to unlock LUKS device {self}')}\n"
            "\n"
            f"{self.post_load()}"
        )

    def unload(self) -> str:
        return (
            f"{self.pre_unload()}"
            f"echo 'Closing LUKS device {self}'\n"
            f"cryptsetup close {quote(self.name)} || "
            f"{_die(f'Failed to close LUKS device {self}')}\n"
            "\n"
            f"{self.post_unload()}"
        )

    def path(self) -> str:
        return quote('/dev/mapper/' + self.name)


class LvmData(Data):
    """LvmData class: LVM logical volume

    Attributes:
    vg_name -- String containing the volume group name
    lv_name -- String containing the logical volume's name
    """

    def __init__(self, vg_name: str, lv_name: str):
        super().__init__()
        self.execs.add(('lvm', None))
        self.vg_name = vg_name
        self.lv_name = lv_name

    def __str__(self) -> str:
        return self.vg_name + "/" + self.lv_name

    def load(self) -> str:
        return (
            f"{self.pre_load()}"
            f"echo 'Enabling LVM logical volume {self}'\n"
            "lvm lvchange --sysinit -a ly "
            f"{quote(f'{self.vg_name}/{self.lv_name}')} || "
            f"{_die(f'Failed to enable LVM logical volume {self}')}\n"
            "lvm vgscan --mknodes || "
            f"{_die(f'Failed to create LVM nodes for {self}')}\n"
            "\n"
            f"{self.post_load()}"
        )

    def unload(self) -> str:
        return (
            f"{self.pre_unload()}"
            f"echo 'Disabling LVM logical volume {self}'\n"
            "lvm lvchange --sysinit -a ln "
            f"{quote(f'{self.vg_name}/{self.lv_name}')} || "
            f"{_die(f'Failed to disable LVM logical volume {self}')}\n"
            "lvm vgscan --mknodes || "
            f"{_die(f'Failed to remove LVM nodes for {self}')}\n"
            "\n"
            f"{self.post_unload()}"
        )

    def path(self) -> str:
        # If LV or VG name has an hyphen '-', LVM doubles it in the path
        return quote('/dev/mapper/' + self.vg_name.replace('-', '--')
                     + '-' + self.lv_name.replace('-', '--'))


class MountData(Data):
    """Data class for mount points

    Attributes:
    source -- Data object to take as source for the mount
    mountpoint -- String: path to use as mountpoint
    options -- String: mount options to use, defaults to "ro"
    """

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

    def load(self) -> str:
        fsck = (
            "FSTAB_FILE=/dev/null "
            f'fsck -t {quote(self.filesystem)} {self.source.path()} || '
            f"{_die(f'Failed to check filesystem {self}')}\n"
            if self.source.path() != 'none'
            else ''
        )
        mkdir = (
            f"[ -d {quote(self.mountpoint)} ] || "
            f"mkdir {quote(self.mountpoint)} || "
            f"{_die(f'Failed to create directory {self}')}\n"
            if os.path.dirname(self.mountpoint) == '/mnt'
            else ''
        )
        return (
            f"{self.pre_load()}"
            f"echo 'Mounting filesystem {self}'\n"
            f"{fsck}"
            f"{mkdir}"
            f"mount -t {quote(self.filesystem)} -o {quote(self.options)} "
            f"{self.source.path()} {quote(self.mountpoint)} || "
            f"{_die(f'Failed to mount filesystem {self}')}\n"
            "\n"
            f"{self.post_load()}"
        )

    def unload(self) -> str:
        return (
            f"{self.pre_unload()}"
            f"echo 'Unmounting filesystem {self}'\n"
            f"umount {quote(self.mountpoint)} || "
            f"{_die(f'Failed to unmount filesystem {self}')}\n"
            "\n"
            f"{self.post_unload()}"
        )

    def path(self) -> str:
        return quote(self.mountpoint)


class MdData(Data):
    """Data class for MD RAID

    Attributes:
    sources -- List of Data objects to use as sources
    name -- Name to use for the RAID
    """

    def __init__(self, sources: List[Data], name: str):
        super().__init__()
        self.execs.add(('mdadm', None))
        self.sources = sources
        self.name = name
        if not self.sources:
            raise DataError(f"{self} has no source defined")

    def __str__(self) -> str:
        return self.name

    def load(self) -> str:
        # Get the string containing all sources to use
        sources_string = ""
        for source in self.sources:
            if isinstance(source, UuidData):
                sources_string += f"--uuid {quote(source.uuid)} "
            else:
                sources_string += f"{source.path()} "
        return (
            f"{self.pre_load()}"
            f"echo 'Assembling MD RAID {self}'\n"
            "MDADM_NO_UDEV=1 "
            f"mdadm --assemble {sources_string}{quote(self.name)} || "
            f"{_die(f'Failed to assemble MD RAID {self}')}\n"
            "\n"
            f"{self.post_load()}"
        )

    def unload(self) -> str:
        return (
            f"{self.pre_unload()}"
            f"echo 'Stopping MD RAID {self}'\n"
            "MDADM_NO_UDEV=1 "
            f"mdadm --stop {quote(self.name)} || "
            f"{_die(f'Failed to stop MD RAID {self}')}\n"
            "\n"
            f"{self.post_unload()}"
        )

    def path(self) -> str:
        return quote('/dev/md/' + self.name)


class CloneData(Data):
    """Data class for cloning data objects

    Attributes:
    source -- Data object: source directory
    dest -- Data object: destination directory
    """

    def __init__(self, source: Data, dest: Data):
        super().__init__()
        self.source = source
        self.dest = dest

    def __str__(self) -> str:
        return f"{self.source} to {self.dest}"

    def load(self) -> str:
        return (
            f"{self.pre_load()}"
            f"echo 'Cloning {self}'\n"
            f"cp -aT {self.source.path()} {self.dest.path()} || "
            f"{_die(f'Failed to clone {self}')}\n"
            "\n"
            f"{self.post_load()}"
        )

    def path(self) -> str:
        return self.dest.path()


def mkinit(root: Data, mounts: Optional[Set[Data]] = None,
           keymap: Optional[str] = None, init: Optional[str] = None) -> str:
    """Create the init script"""
    if mounts is None:
        mounts = set()
    if init is None:
        init = '/sbin/init'

    script = [do_header(), do_init(), do_cmdline()]
    if keymap is not None:
        script.append(do_keymap(
            keymap if keymap else '/root/keymap.bmap'
        ))
    script.append(root.load())
    script.append(do_maintenance())
    for mount in mounts:
        script.append(mount.load())
    script.append(do_switch_root(init, root))
    return ''.join(script)


def entry_point() -> None:
    """Main entry point of the module"""
    config = read_config()
    print(mkinit(
        root=config['root'], mounts=config['mounts'],
        keymap=(None if config['keymap_src'] is None
                else '' if config['keymap_dest'] is None
                else config['keymap_dest']),
        init=config['init']
    ))
