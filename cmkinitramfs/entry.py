"""Entry point module for cmkinitramfs"""

from __future__ import annotations

import argparse
import configparser
import itertools
import locale
import logging
import os
import shlex
import shutil
import sys
from dataclasses import dataclass
from typing import Dict, FrozenSet, Optional, Tuple, overload

import cmkinitramfs
import cmkinitramfs.data as datamod
import cmkinitramfs.initramfs as mkramfs
from cmkinitramfs.bin import findlib, findlib_iter
from cmkinitramfs.init import mkinit

logger = logging.getLogger(__name__)
_VERSION_INFO = \
    f"%(prog)s ({cmkinitramfs.__name__}) {cmkinitramfs.__version__}"


def _find_config_file() -> Optional[str]:
    """Find a configuration file to use"""
    if os.environ.get('CMKINITCFG'):
        return os.environ['CMKINITCFG']
    if os.path.isfile('./cmkinitramfs.ini'):
        return './cmkinitramfs.ini'
    if os.path.isfile('/etc/cmkinitramfs.ini'):
        return '/etc/cmkinitramfs.ini'
    return None


@dataclass(frozen=True)
class Config:
    """Configuration informations

    :param root: Rootfs data needed to boot
    :param mounts: Non-rootfs datas needed to boot
    :param keymap: Keymap information tuple ``(source, build, dest)``:
        ``source``: keymap to convert, ``build``: converted keymap,
        ``dest``: keymap path within the initramfs
    :param init: Init path to launch at the end of the init script
        (``switch_root``)
    :param files: User configured files,
        see :attr:`cmkinitramfs.init.Data.files`
    :param execs: User configured executables,
        see :attr:`cmkinitramfs.init.Data.files`
    :param libs: User configured libraries,
        see :attr:`cmkinitramfs.init.Data.files`
    :param init_path: Path where the init script will be generated
    :param cmkcpiodir_opts: Default options for cmkcpiodir
    :param cmkcpiolist_opts: Default options for cmkcpiolist
    :param modules: Kernel modules to be loaded in the initramfs:
        ``(module, (arg, ...))``. ``module`` is the module name string,
        and ``(arg, ...)``` is the tuple with the module parameters.
    """
    root: datamod.Data
    mounts: FrozenSet[datamod.Data]
    keymap: Optional[Tuple[str, str, str]]
    init: str
    files: FrozenSet[Tuple[str, Optional[str]]]
    execs: FrozenSet[Tuple[str, Optional[str]]]
    libs: FrozenSet[Tuple[str, Optional[str]]]
    init_path: str
    cmkcpiodir_opts: str
    cmkcpiolist_opts: str
    modules: FrozenSet[Tuple[str, Tuple[str, ...]]]


def read_config(config_file: Optional[str] = _find_config_file()) -> Config:
    """Read a configuration file and generate data structures from it

    :param config_file: Configuration file to use. Defaults to, in order:
        ``CMKINITCFG`` environment variable, ``./cmkinitramfs.ini``,
        ``/etc/cmkinitramfs.ini``.
    :return: Configuration dictionnary, described by :class:`Config`
    """

    @overload
    def find_data(data_str: None) -> None: ...
    @overload
    def find_data(data_str: str) -> datamod.Data: ...

    def find_data(data_str: Optional[str]) -> Optional[datamod.Data]:
        """Find a Data object from a data string"""
        if data_str is None:
            return None
        if data_str[:5] == 'UUID=':
            if data_dic.get(data_str[5:]) is None:
                data_dic[data_str[5:]] = datamod.UuidData(data_str[5:])
            return data_dic[data_str[5:]]
        if data_str[:5] == 'PATH=':
            if data_dic.get(data_str[5:]) is None:
                data_dic[data_str[5:]] = datamod.PathData(data_str[5:])
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
    data_dic: Dict[str, datamod.Data] = {}
    for data_id in config.sections():
        data_config = config[data_id]
        if data_config['type'] == 'luks':
            data_dic[data_id] = datamod.LuksData(
                find_data(data_config['source']),
                data_config['name'],
                find_data(data_config.get('key')),
                find_data(data_config.get('header')),
                data_config.getboolean('discard', fallback=False),
            )
        elif data_config['type'] == 'lvm':
            data_dic[data_id] = datamod.LvmData(
                data_config['vg-name'],
                data_config['lv-name'],
            )
        elif data_config['type'] == 'mount':
            data_dic[data_id] = datamod.MountData(
                find_data(data_config['source']),
                data_config['mountpoint'],
                data_config['filesystem'],
                data_config.get('options', 'ro'),
            )
        elif data_config['type'] == 'md':
            data_dic[data_id] = datamod.MdData(
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

    # Define Data for root and for other mounts
    root = find_data(config['DEFAULT']['root'])
    mounts = set(
        find_data(k.strip())
        for k in config['DEFAULT']['mountpoints'].strip().split(',')
    )

    # Define needed files, execs and libs
    files = set()
    for data in itertools.chain({root}, mounts):
        files |= data.files
        for ddep in data.iter_all_deps():
            files |= ddep.files
    for line in config['DEFAULT'].get('files', '').split('\n'):
        if line:
            src, *dest = line.split(':')
            files.add((src, dest[0] if dest else None))
    execs = set()
    for data in itertools.chain({root}, mounts):
        execs |= data.execs
        for ddep in data.iter_all_deps():
            execs |= ddep.execs
    for line in config['DEFAULT'].get('execs', '').split('\n'):
        if line:
            src, *dest = line.split(':')
            execs.add((src, dest[0] if dest else None))
    libs = set()
    for data in itertools.chain({root}, mounts):
        libs |= data.libs
        for ddep in data.iter_all_deps():
            libs |= ddep.libs
    for line in config['DEFAULT'].get('libs', '').split('\n'):
        if line:
            src, *dest = line.split(':')
            libs.add((src, dest[0] if dest else None))

    modules = set()
    for module in config['DEFAULT'].get('modules', '').split('\n'):
        if module:
            mod_name, *mod_args = module.split()
            modules.add((mod_name, tuple(mod_args)))

    # Create dictionnary to return
    ret_cfg = Config(
        root=root,
        mounts=frozenset(mounts),
        keymap=(
            config['DEFAULT'].get('keymap-src'),
            config['DEFAULT'].get('keymap-path', '/tmp/keymap.bmap'),
            config['DEFAULT'].get('keymap-dest', '/root/keymap.bmap'),
        ) if config['DEFAULT'].getboolean('keymap', fallback=False) else None,
        init=config['DEFAULT'].get('init', '/sbin/init'),
        files=frozenset(files),
        execs=frozenset(execs),
        libs=frozenset(libs),
        init_path=config['DEFAULT'].get('init-path', '/tmp/init.sh'),
        cmkcpiodir_opts=config['DEFAULT'].get(
            'cmkcpiodir-default-opts', ''
        ),
        cmkcpiolist_opts=config['DEFAULT'].get(
            'cmkcpiolist-default-opts', ''
        ),
        modules=frozenset(modules),
    )

    # Configure final data sources
    for data in ret_cfg.mounts | {ret_cfg.root}:
        data.set_final()

    return ret_cfg


def entry_cmkinit() -> None:
    """Main entry point of the module"""
    config = read_config()
    parser = argparse.ArgumentParser(description="Build an init script")
    parser.add_argument('--version', action='version', version=_VERSION_INFO)
    parser.parse_args()
    mkinit(
        out=sys.stdout,
        root=config.root,
        mounts=config.mounts,
        keymap=(None if config.keymap is None else config.keymap[2]),
        init=config.init,
        modules=config.modules,
    )


def _common_parser_logging(verbose: bool = False, quiet: int = 0) \
        -> argparse.ArgumentParser:
    """Create the common parser for entry points with a logger

    :param verbose: Default verbose value
    :param quiet: Default quiet value
    """
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument(
        '--verbose', '-v', action='store_true', default=verbose,
        help="be verbose",
    )
    parser.add_argument(
        '--quiet', '-q', action='count', default=quiet,
        help="be quiet (can be repeated)",
    )
    return parser


def _set_logging_level(verbose: bool, quiet: int) -> None:
    """Set global logging level according to verbose and quiet"""
    if verbose:
        level = logging.DEBUG
    elif quiet >= 3:
        level = logging.CRITICAL
    elif quiet >= 2:
        level = logging.ERROR
    elif quiet >= 1:
        level = logging.WARNING
    else:
        level = logging.INFO
    logging.getLogger().setLevel(level)


def entry_findlib() -> None:
    """Entry point for the findlib utility"""

    parser = argparse.ArgumentParser(
        description="Find a library on the system",
        parents=(_common_parser_logging(),),
    )
    parser.add_argument('--version', action='version', version=_VERSION_INFO)
    parser.add_argument(
        '--compatible', '-c', type=str, default=None,
        help="set a binary the library must be compatible with",
    )
    parser.add_argument(
        '--root', '-r', type=str, default='/',
        help="set the root directory to search for the library",
    )
    parser.add_argument(
        '--null', '-0', action='store_true', default=False,
        help="paths will be delemited by null characters instead of newlines",
    )
    parser.add_argument(
        '--glob', '-g', action='store_true', default=False,
        help="library names are glob patterns",
    )
    parser.add_argument(
        'libs', metavar='LIB', type=str, nargs='+',
        help="library to search",
    )
    args = parser.parse_args()
    _set_logging_level(args.verbose, args.quiet + 1)

    errors = False
    for lib in args.libs:
        logger.info("Searching library: %s", lib)

        try:
            lib_iter = \
                (findlib(lib, compat=args.compatible, root=args.root),) \
                if not args.glob \
                else findlib_iter(lib, compat=args.compatible, root=args.root)
            for found, _ in lib_iter:
                if args.quiet < 3:
                    print(found, end=('\n' if not args.null else '\0'))
        except FileNotFoundError:
            logger.error("%s: Library not found", lib)
            errors = True
            continue
    sys.exit(0 if not errors else 1)


def _common_parser_cmkcpio() -> argparse.ArgumentParser:
    """Create the common parser for cmkcpio* entry points"""
    parser = argparse.ArgumentParser(
        add_help=False,
        parents=(_common_parser_logging(),),
    )
    parser.add_argument(
        '--version', action='version', version=_VERSION_INFO
    )
    parser.add_argument(
        "--debug", "-d", action="store_true", default=False,
        help="debugging mode: non-root, implies -k"
    )
    parser.add_argument(
        "--output", "-o", type=str, default='/usr/src/initramfs.cpio',
        help="set the output of the CPIO archive"
    )
    parser.add_argument(
        '--binroot', '-r', type=str, default='/',
        help="set the root directory for binaries (executables and libraries)"
    )
    parser.add_argument(
        '--kernel', '-K', type=str, default=None,
        help=("set the target kernel version of the initramfs, "
              "defaults to the running kernel")
    )
    return parser


def entry_cmkcpiolist() -> None:
    """Entry point for cmkcpiolist"""

    # Load configuration
    config = read_config()

    # Arguments
    parser = argparse.ArgumentParser(
        description="Build an initramfs using a CPIO list",
        parents=(_common_parser_cmkcpio(),)
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        '--only-build-archive', '-c', action="store_true", default=False,
        help="only build the CPIO archive from an existing CPIO list"
    )
    group.add_argument(
        '--only-build-list', '-L', action="store_true", default=False,
        help="only build the CPIO list, implies -k"
    )
    parser.add_argument(
        '--keep', '-k', action="store_true", default=False,
        help="keep the created CPIO list"
    )
    parser.add_argument(
        '--cpio-list', '-l', type=str, default='/tmp/initramfs.list',
        help="set the location of the CPIO list"
    )
    args = parser.parse_args(
        shlex.split(config.cmkcpiolist_opts, posix=True) + sys.argv[1:]
    )

    _set_logging_level(args.verbose, args.quiet)

    # Parse arguments
    if args.debug or args.only_build_list:
        args.keep = True

    # Keymap
    if config.keymap is not None and config.keymap[0] is not None:
        with open(config.keymap[1], 'wb') as keymap_bin:
            mkramfs.keymap_build(
                config.keymap[0], keymap_bin,
                unicode=(locale.getdefaultlocale()[1] == 'UTF-8')
            )

    # Init
    with open(config.init_path, 'w') as init_file:
        mkinit(
            out=init_file,
            root=config.root,
            mounts=config.mounts,
            keymap=(None if config.keymap is None else config.keymap[2]),
            init=config.init,
            modules=config.modules,
        )

    # Initramfs
    if not args.only_build_archive:
        assert config.init_path is not None
        logger.info("Creating initramfs")
        initramfs = mkramfs.Initramfs(
            user=(0 if not args.debug else os.getuid()),
            group=(0 if not args.debug else os.getgid()),
            binroot=args.binroot,
            kernel=args.kernel,
        )
        mkramfs.mkinitramfs(
            initramfs=initramfs,
            init=config.init_path,
            files=config.files,
            execs=config.execs,
            libs=config.libs,
            keymap=(None if config.keymap is None
                    else config.keymap[1:3]),
            modules=(k[0] for k in config.modules),
        )

        # CPIO list
        logger.info("Generating CPIO list")
        if args.cpio_list == '-' and args.only_build_list:
            initramfs.build_to_cpio_list(sys.stdout)
        else:
            with open(args.cpio_list, 'w') as cpiolist:
                initramfs.build_to_cpio_list(cpiolist)

    if not args.only_build_list:
        # Build CPIO archive
        logger.info("Generating CPIO archive to %s", args.output)
        if args.output == '-':
            mkramfs.mkcpio_from_list(args.cpio_list, sys.stdout.buffer)
        else:
            with open(args.output, 'wb') as cpiodest:
                mkramfs.mkcpio_from_list(args.cpio_list, cpiodest)

    if not args.keep:
        # Cleanup temporary files
        if os.path.exists(args.cpio_list):
            logger.info("Cleaning %s", args.cpio_list)
            os.remove(args.cpio_list)
        if config.keymap is not None and os.path.exists(config.keymap[1]):
            logger.info("Cleaning %s", config.keymap[1])
            os.remove(config.keymap[1])
        if os.path.exists(config.init_path):
            logger.info("Cleaning %s", config.init_path)
            os.remove(config.init_path)


def entry_cmkcpiodir() -> None:
    """Entry point for cmkcpiodir"""

    # Load configuration
    config = read_config()

    # Arguments
    parser = argparse.ArgumentParser(
        description="Build an initramfs using a directory.",
        parents=(_common_parser_cmkcpio(),)
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        '--only-build-archive', '-c', action="store_true", default=False,
        help="only build the CPIO archive from an existing initramfs directory"
    )
    group.add_argument(
        '--only-build-directory', '-D', action="store_true", default=False,
        help="only build the initramfs directory, implies -k"
    )
    parser.add_argument(
        '--keep', '-k', action="store_true", default=False,
        help="keep the created initramfs directory"
    )
    parser.add_argument(
        "--clean", "-C", action="store_true", default=False,
        help="overwrite temporary directory if it exists, use carefully"
    )
    parser.add_argument(
        '--build-dir', '-b', type=str, default='/tmp/initramfs',
        help="set the location of the initramfs directory"
    )
    args = parser.parse_args(
        shlex.split(config.cmkcpiodir_opts, posix=True) + sys.argv[1:]
    )

    _set_logging_level(args.verbose, args.quiet)

    # Parse arguments
    if args.debug or args.only_build_directory:
        args.keep = True

    # Keymap
    if config.keymap is not None and config.keymap[0] is not None:
        with open(config.keymap[1], 'wb') as keymap_bin:
            mkramfs.keymap_build(
                config.keymap[0], keymap_bin,
                unicode=(locale.getdefaultlocale()[1] == 'UTF-8')
            )

    # Init
    with open(config.init_path, 'w') as init_file:
        mkinit(
            out=init_file,
            root=config.root,
            mounts=config.mounts,
            keymap=(None if config.keymap is None else config.keymap[2]),
            init=config.init,
            modules=config.modules,
        )

    # Initramfs
    if not args.only_build_archive:
        assert config.init_path is not None
        logger.info("Creating initramfs")
        initramfs = mkramfs.Initramfs(
            user=(0 if not args.debug else os.getuid()),
            group=(0 if not args.debug else os.getgid()),
            binroot=args.binroot,
            kernel=args.kernel,
        )
        mkramfs.mkinitramfs(
            initramfs=initramfs,
            init=config.init_path,
            files=config.files,
            execs=config.execs,
            libs=config.libs,
            keymap=(None if config.keymap is None
                    else config.keymap[1:3]),
            modules=(k[0] for k in config.modules),
        )

    if not args.only_build_archive:
        # Pre-build cleanup
        if args.clean and os.path.exists(args.build_dir):
            logger.warning("Overwriting %s", args.build_dir)
            shutil.rmtree(args.build_dir)

        # Build
        logger.info("Building initramfs to directory %s", args.build_dir)
        initramfs.build_to_directory(args.build_dir,
                                     do_nodes=(not args.debug))

    if not args.only_build_directory:
        # Create CPIO archive
        logger.info("Generating CPIO archive to %s from %s",
                    args.output, args.build_dir)
        if args.output == '-':
            mkramfs.mkcpio_from_dir(args.build_dir, sys.stdout.buffer)
        else:
            with open(args.output, 'wb') as cpiodest:
                mkramfs.mkcpio_from_dir(args.build_dir, cpiodest)

    if not args.keep:
        # Cleanup temporary files
        if os.path.exists(args.build_dir):
            logger.info("Cleaning %s", args.build_dir)
            shutil.rmtree(args.build_dir)
        if config.keymap is not None and os.path.exists(config.keymap[1]):
            logger.info("Cleaning %s", config.keymap[1])
            os.remove(config.keymap[1])
        if os.path.exists(config.init_path):
            logger.info("Cleaning %s", config.init_path)
            os.remove(config.init_path)
