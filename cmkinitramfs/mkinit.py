"""Library providing functions and classes to build an /init script

do_xxx() functions will return a string for the xxx action. This string is to
be written into the /init script.

_fun_xxx() functions return a string to declare the xxx function available
from within the /init script.

The Data class defines an abstract object containing data, it has multiple
herited classes for multiple source of data.
"""

import configparser
import os.path


def _fun_rescue_shell():
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


def _fun_printk():
    """Outputs a string to kernel log and to stderr"""
    return (
        "printk()\n"
        "{\n"
        "\techo \"initramfs: $1\" 1>/dev/kmsg\n"
        "\techo \"$1\" 1>&2\n"
        "}\n"
    )


def _die(message):
    """Returns a string stopping the boot process
    This function will load a rescue shell with an error message,
    this is an abstraction for the rescue_shell function
    """
    return f"rescue_shell 'FATAL: {message}'"


def do_header(home="/root", path="/bin:/sbin"):
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
        f"HOME='{home}'\n"
        "export HOME\n"
        f"PATH='{path}'\n"
        "export PATH\n"
        "\n"
        f"{_fun_rescue_shell()}\n"
        f"{_fun_printk()}\n"
        "echo 'INITRAMFS: Start'\n"
        "\n"
    )


def do_init():
    """Initialize the init environment
    This action will:
      - Check current PID is 1
      - Mount /proc, /sys, /dev
      - Set kernel log level to 3
    """
    return (
        "echo 'Initialization'\n"
        "test $$ -eq 1 || "
        f"{_die('init expects to be run as PID 1, current PID is $$')}\n"
        "mount -t proc none /proc || "
        f"{_die('Failed to mount /proc')}\n"
        "mount -t sysfs none /sys || "
        f"{_die('Failed to mount /sys')}\n"
        "mount -t devtmpfs none /dev || "
        f"{_die('Failed to mount /dev')}\n"
        "echo 3 1>'/proc/sys/kernel/printk'\n"
        "\n"
    )


def do_cmdline():
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


def do_keymap(keymap_file):
    """Load the keymap
    keymap_file -- String: absolute path to the keymap file
    within the initramfs
    """
    return (
        "echo 'Loading keymap'\n"
        f"[ -f '{keymap_file}' ] || "
        f"{_die(f'Failed to load keymap, file {keymap_file} not found')}\n"
        f"loadkmap <'{keymap_file}' || "
        f"{_die(f'Failed to load keymap {keymap_file}')}\n"
        "\n"
    )


def do_maintenance():
    """Check for maintenance
    If the MAINTENANCE variable is set, load a rescue shell
    """
    return (
        "[ -n \"${MAINTENANCE}\" ] && "
        "rescue_shell 'Going into maintenance mode'\n"
        "\n"
    )


def do_switch_root(init, newroot):
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
        "awk '{ print $4 }' '/proc/sys/kernel/printk' "
        "1>'/proc/sys/kernel/printk'\n"
        f"umount /dev || {_die('Failed to unmount /dev')}\n"
        f"umount /proc || {_die('Failed to unmount /proc')}\n"
        f"umount /sys || {_die('Failed to unmount /sys')}\n"
        "echo 'INITRAMFS: End'\n"
        f"exec switch_root \"{newroot.path()}\" '{init}'\n"
    )


class Data:
    """Data class for any object representing Data on the system
    This is an abstract class representing an object containing data.
    It has two main methods: load() and unload(), and several related
    methods.
    The method set_final() declare the object as required for the final
    boot environment (e.g. rootfs).

    Private attributes:
    _need -- List of Data objects needed
    _lneed -- List of Data objects needed for load (those objects
      can be unloaded once the current object is loaded)
    _needed_by -- List of Data objects depending on this object
    _is_final -- bool: Does the data is needed by the final boot environment?
    _is_loaded -- bool: Data is currently loaded
    """

    def __init__(self):
        self._need = []
        self._lneed = []
        self._is_final = False
        self._is_loaded = False
        self._needed_by = []

    def is_final(self):
        """Returns a boolean indicating if the data is final"""
        return self._is_final

    def set_final(self):
        """This function set the data object as final
        This means the data is required by the final boot environment
        and should never be unloaded (as it would be pointless).
        This will also flag its hard dependencies as final.
        """
        self._is_final = True
        for k in self._need:
            k.set_final()

    def add_dep(self, dep):
        """Add a Data object to the hard dependencies list"""
        self._need.append(dep)
        dep._needed_by.append(self)

    def add_load_dep(self, dep):
        """Add a Data object to the loading dependencies list"""
        self._lneed.append(dep)
        dep._needed_by.append(self)

    def pre_load(self):
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

    def post_load(self):
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

    def load(self):
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

    def pre_unload(self):
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

    def post_unload(self):
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

    def unload(self):
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

    def path(self):
        """Get the path of this data
        This function provides a string allowing access to data from /init,
        this string can be a path or a command in a subshell (e.g.
        $(findfs UUID=foobar)).
        This **has** to be implemented by subclasses.
        """
        raise NotImplementedError()


class DataError(Exception):
    """Error in the Data object"""
    pass


class PathData(Data):
    """PathData class: Absolute path

    Attributes:
    filepath -- String: path of the data
    """

    def __init__(self, path):
        super().__init__()
        self.filepath = path

    def __str__(self):
        return self.filepath

    def path(self):
        return self.filepath


class UuidData(Data):
    """UuidData class: UUID for device

    Attributes:
    uuid -- String: UUID of the data
    """

    def __init__(self, uuid):
        super().__init__()
        self.uuid = uuid

    def __str__(self):
        return "UUID=" + self.uuid

    def path(self):
        return "$(findfs 'UUID=" + self.uuid + "')"


class LuksData(Data):
    """LuksData class: LUKS encrypted partition

    Attributes:
    source -- Data to unlock (crypto_LUKS volume)
    name -- String: name used by LUKS for the device
    key -- Data to use as key, defaults to None: no key file
    header -- Data to use as header, defaults to None: not needed
    """

    def __init__(self, source, name, key=None, header=None):
        super().__init__()
        self.source = source
        self.name = name
        self.key = key
        self.header = header

    def __str__(self):
        return self.name

    def load(self):
        header = f'--header "{self.header.path()}" ' if self.header else ''
        key_file = f'--key-file "{self.key.path()}" ' if self.key else ''
        return (
            f"{self.pre_load()}"
            f"echo 'Unlocking LUKS device {self}'\n"
            f"cryptsetup luksOpen {header}{key_file}"
            f"\"{self.source.path()}\" '{self.name}' || "
            f"{_die(f'Failed to unlock LUKS device {self}')}\n"
            "\n"
            f"{self.post_load()}"
        )

    def unload(self):
        return (
            f"{self.pre_unload()}"
            f"echo 'Closing LUKS device {self}'\n"
            f"cryptsetup luksClose '{self.name}' || "
            f"{_die(f'Failed to close LUKS device {self}')}\n"
            "\n"
            f"{self.post_unload()}"
        )

    def path(self):
        return "/dev/mapper/" + self.name


class LvmData(Data):
    """LvmData class: LVM logical volume

    Attributes:
    vg_name -- String containing the volume group name
    lv_name -- String containing the logical volume's name
    """

    def __init__(self, vg_name, lv_name):
        super().__init__()
        self.vg_name = vg_name
        self.lv_name = lv_name

    def __str__(self):
        return self.vg_name + "/" + self.lv_name

    def load(self):
        return (
            f"{self.pre_load()}"
            f"echo 'Enabling LVM logical volume {self}'\n"
            "lvm lvchange --sysinit -a ly "
            f"'{self.vg_name}/{self.lv_name}' || "
            f"{_die(f'Failed to enable LVM logical volume {self}')}\n"
            "lvm vgscan --mknodes || "
            f"{_die(f'Failed to create LVM nodes for {self}')}\n"
            "\n"
            f"{self.post_load()}"
        )

    def unload(self):
        return (
            f"{self.pre_unload()}"
            f"echo 'Disabling LVM logical volume {self}'\n"
            "lvm lvchange --sysinit -a ln "
            f"'{self.vg_name}/{self.lv_name}' || "
            f"{_die(f'Failed to disable LVM logical volume {self}')}\n"
            "lvm vgscan --mknodes || "
            f"{_die(f'Failed to remove LVM nodes for {self}')}\n"
            "\n"
            f"{self.post_unload()}"
        )

    def path(self):
        # If LV or VG name has an hyphen '-', LVM doubles it in the path
        if '-' in self.vg_name + self.lv_name:
            return "/dev/mapper/" + self.vg_name.replace('-', '--')\
                   + "-" + self.lv_name.replace('-', '--')
        return "/dev/mapper/" + self.vg_name + "-" + self.lv_name


class MountData(Data):
    """Data class for mount points

    Attributes:
    source -- Data object to take as source for the mount
    mountpoint -- String: path to use as mountpoint
    options -- String: mount options to use, defaults to "ro"
    """

    def __init__(self, source, mountpoint, filesystem, options="ro"):
        super().__init__()
        self.source = source if source else PathData("none")
        self.mountpoint = mountpoint
        self.filesystem = filesystem
        self.options = options

    def __str__(self):
        return self.mountpoint

    def load(self):
        fsck = (
            "FSTAB_FILE='/dev/null' "
            f'fsck -t {self.filesystem} "{self.source.path()}" || '
            f"{_die(f'Failed to check filesystem {self}')}\n"
            if self.source.path() != 'none'
            else ''
        )
        mkdir = (
            f"[ -d '{self.mountpoint}' ] || "
            f"mkdir '{self.mountpoint}' || "
            f"{_die(f'Failed to create directory {self}')}\n"
            if os.path.dirname(self.mountpoint) == '/mnt'
            else ''
        )
        return (
            f"{self.pre_load()}"
            f"echo 'Mounting filesystem {self}'\n"
            f"{fsck}"
            f"{mkdir}"
            f"mount -t {self.filesystem} -o '{self.options}' "
            f"\"{self.source.path()}\" '{self.mountpoint}' || "
            f"{_die(f'Failed to mount filesystem {self}')}\n"
            "\n"
            f"{self.post_load()}"
        )

    def unload(self):
        return (
            f"{self.pre_unload()}"
            f"echo 'Unmounting filesystem {self}'\n"
            f"umount '{self.mountpoint}' || "
            f"{_die(f'Failed to unmount filesystem {self}')}\n"
            "\n"
            f"{self.post_unload()}"
        )

    def path(self):
        return self.mountpoint


class MdData(Data):
    """Data class for MD RAID

    Attributes:
    sources -- List of Data objects to use as sources
    name -- Name to use for the RAID
    """

    def __init__(self, sources, name):
        super().__init__()
        self.sources = sources
        self.name = name
        if not self.sources:
            raise DataError(f"{self} has no source defined")

    def __str__(self):
        return self.name

    def load(self):
        # Get the string containing all sources to use
        sources_string = ""
        for source in self.sources:
            if isinstance(source, UuidData):
                sources_string += f"--uuid \"{source.uuid}\" "
            else:
                sources_string += f"\"{source.path()}\" "
        return (
            f"{self.pre_load()}"
            f"echo 'Assembling MD RAID {self}'\n"
            "MDADM_NO_UDEV=1 "
            f"mdadm --assemble {sources_string}'{self.name}' || "
            f"{_die(f'Failed to assemble MD RAID {self}')}\n"
            "\n"
            f"{self.post_load()}"
        )

    def unload(self):
        return (
            f"{self.pre_unload()}"
            f"echo 'Stopping MD RAID {self}'\n"
            "MDADM_NO_UDEV=1 "
            f"mdadm --stop '{self.name}' || "
            f"{_die(f'Failed to stop MD RAID {self}')}\n"
            "\n"
            f"{self.post_unload()}"
        )

    def path(self):
        return "/dev/md/" + self.name


class CloneData(Data):
    """Data class for cloning data objects

    Attributes:
    source -- Data object: source directory
    dest -- Data object: destination directory
    """

    def __init__(self, source, dest):
        super().__init__()
        self.source = source
        self.dest = dest

    def __str__(self):
        return f"{self.source} to {self.dest}"

    def load(self):
        return (
            f"{self.pre_load()}"
            f"echo 'Cloning {self}'\n"
            f"cp -aT \"{self.source.path()}\" \"{self.dest.path()}\" || "
            f"{_die(f'Failed to clone {self}')}\n"
            "\n"
            f"{self.post_load()}"
        )

    def path(self):
        return self.dest.path()


def mkinit(root, mounts=None, keymap_src=None, keymap_dest=None,
           init='/sbin/init'):
    """Create the init script"""
    if mounts is None:
        mounts = []

    script = [do_header(), do_init(), do_cmdline()]
    if keymap_src is not None:
        script.append(keymap_dest if keymap_dest is not None
                    else '/root/keymap.bmap')
    script.append(root.load())
    script.append(do_maintenance())
    for mount in mounts:
        script.append(mount.load())
    script.append(do_switch_root(init, root))
    return ''.join(script)


def _find_config_file():
    """Find a configuration file to use"""
    if os.environ.get('CMKINITCFG'):
        return os.environ['CMKINITCFG']
    if os.path.isfile('./cmkinitramfs.ini'):
        return './cmkinitramfs.ini'
    if os.path.isfile('/etc/cmkinitramfs.ini'):
        return '/etc/cmkinitramfs.ini'
    return None


def read_config(config_file=_find_config_file()):
    """Read a configuration file and generate data structures from it"""
    def find_data(data_str):
        """Find a Data object from a data string"""
        if data_str is None:
            return None

        if data_str[:5] == 'UUID=':
            if data_dic.get(data_str[5:]) is None:
                data_dic[data_str[5:]] = UuidData(data_str[5:])
            return data_dic[data_str[5:]]
        if data_str[:5] == 'PATH=':
            if data_dic.get(data_str[5:]) is None:
                data_dic[data_str[5:]] = PathData(data_str[5:])
            return data_dic[data_str[5:]]
        if data_str[:5] == 'DATA=':
            return data_dic[data_str[5:]]
        return data_dic[data_str]

    # Read config file
    config = configparser.ConfigParser()
    if config_file is None:
        raise FileNotFoundError(f"Configuration file {config_file} not found")
    config.read(config_file)

    # Get all data sources in data_dic
    data_dic = {}
    for data_id in config.sections():
        data_config = config[data_id]
        if data_config['type'] == 'luks':
            data_dic[data_id] = LuksData(
                find_data(data_config['source']),
                data_config['name'],
                find_data(data_config.get('key')),
                find_data(data_config.get('header')),
            )
        elif data_config['type'] == 'lvm':
            data_dic[data_id] = LvmData(
                data_config['vg-name'],
                data_config['lv-name'],
            )
        elif data_config['type'] == 'mount':
            data_dic[data_id] = MountData(
                find_data(data_config['source']),
                data_config['mountpoint'],
                data_config['filesystem'],
                data_config.get('options'),
            )
        elif data_config['type'] == 'md':
            data_dic[data_id] = MdData(
                [find_data(k.strip())
                 for k in data_config['source'].strip().split('\n')],
                data_config['name'],
            )
        else:
            raise Exception(f"Unknown config type {data_config['type']}")

    # Configure dependencies
    for data_id, data in data_dic.items():
        if data_id not in config.sections():
            continue
        data_config = config[data_id]
        for dep in data_config['need'].strip().split(','):
            if dep.strip():
                data.add_dep(find_data(dep.strip()))
        for ldep in data_config['load-need'].strip().split(','):
            if ldep.strip():
                data.add_load_dep(find_data(ldep.strip()))

    # Get root and mountpoints
    root = find_data(config['DEFAULT']['root'])
    mounts = [find_data(k.strip())
              for k in config['DEFAULT']['mountpoints'].strip().split(',')]
    keymap_src = config['DEFAULT'].get('keyma')
    keymap_dest = config['DEFAULT'].get('keymap-file', '/root/keymap.bmap')
    init = config['DEFAULT'].get('init', '/sbin.init')

    # Configure final data sources
    for data in mounts + [root]:
        data.set_final()

    return (root, mounts, keymap_src, keymap_dest, init)


def entry_point():
    """Main entry point of the module"""
    print(mkinit(*read_config()))
