"""Utility library for cmkinitramfs"""

import configparser
import logging
import os
from typing import Dict, Optional, Set, TypedDict

import cmkinitramfs.mkinit as mkinit

logger = logging.getLogger(__name__)


def _find_config_file() -> Optional[str]:
    """Find a configuration file to use"""
    if os.environ.get('CMKINITCFG'):
        return os.environ['CMKINITCFG']
    if os.path.isfile('./cmkinitramfs.ini'):
        return './cmkinitramfs.ini'
    if os.path.isfile('/etc/cmkinitramfs.ini'):
        return '/etc/cmkinitramfs.ini'
    return None


class Config(TypedDict):
    root: 'mkinit.Data'
    mounts: Set['mkinit.Data']
    keymap_src: str
    keymap_dest: str
    init: str
    build_dir: str
    files: Set[str]
    execs: Set[str]
    libs: Set[str]


def read_config(config_file: Optional[str] = _find_config_file()) -> Config:
    """Read a configuration file and generate data structures from it"""

    def find_data(data_str: str) -> 'mkinit.Data':
        """Find a Data object from a data string"""
        if data_str[:5] == 'UUID=':
            if data_dic.get(data_str[5:]) is None:
                data_dic[data_str[5:]] = mkinit.UuidData(data_str[5:])
            return data_dic[data_str[5:]]
        if data_str[:5] == 'PATH=':
            if data_dic.get(data_str[5:]) is None:
                data_dic[data_str[5:]] = mkinit.PathData(data_str[5:])
            return data_dic[data_str[5:]]
        if data_str[:5] == 'DATA=':
            return data_dic[data_str[5:]]
        return data_dic[data_str]

    def find_data_opt(data_str: Optional[str]) -> Optional['mkinit.Data']:
        """find_data, returns None if data_str is None"""
        if data_str is not None:
            return find_data(data_str)
        return None

    # Read config file
    config = configparser.ConfigParser()
    if config_file is None:
        raise FileNotFoundError(f"Configuration file {config_file} not found")
    config.read(config_file)

    # Get all data sources in data_dic
    data_dic: Dict[str, 'mkinit.Data'] = {}
    for data_id in config.sections():
        data_config = config[data_id]
        if data_config['type'] == 'luks':
            data_dic[data_id] = mkinit.LuksData(
                find_data(data_config['source']),
                data_config['name'],
                find_data_opt(data_config.get('key')),
                find_data_opt(data_config.get('header')),
                data_config.getboolean('discard', fallback=False),
            )
        elif data_config['type'] == 'lvm':
            data_dic[data_id] = mkinit.LvmData(
                data_config['vg-name'],
                data_config['lv-name'],
            )
        elif data_config['type'] == 'mount':
            data_dic[data_id] = mkinit.MountData(
                find_data(data_config['source']),
                data_config['mountpoint'],
                data_config['filesystem'],
                data_config.get('options'),
            )
        elif data_config['type'] == 'md':
            data_dic[data_id] = mkinit.MdData(
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
    files = root.deps_files().union(*(k.deps_files() for k in mounts))
    for line in config['DEFAULT'].get('files', '').split('\n'):
        if line:
            src, *dest = line.split(':')
            dest = dest[0] if len(dest) else None
            files.add((src, dest))
    execs = root.deps_execs().union(*(k.deps_execs() for k in mounts))
    for line in config['DEFAULT'].get('execs', '').split('\n'):
        if line:
            src, *dest = line.split(':')
            dest = dest[0] if len(dest) else None
            execs.add((src, dest))
    libs = root.deps_libs().union(*(k.deps_libs() for k in mounts))
    for line in config['DEFAULT'].get('libs', '').split('\n'):
        if line:
            src, *dest = line.split(':')
            dest = dest[0] if len(dest) else None
            libs.add((src, dest))

    # Create dictionnary to return
    ret_dic: Config = {
        'root': root,
        'mounts': mounts,
        'keymap_src': config['DEFAULT'].get('keymap'),
        'keymap_dest': config['DEFAULT'].get('keymap-file',
                                             '/root/keymap.bmap'),
        'init': config['DEFAULT'].get('init', '/sbin/init'),
        'build_dir': config["DEFAULT"].get("build-dir"),
        'files': files,
        'execs': execs,
        'libs': libs,
    }

    # Configure final data sources
    for data in ret_dic['mounts'] | {ret_dic['root']}:
        data.set_final()

    logger.debug("Parsed config file %s: %s", config_file, ret_dic)
    return ret_dic
