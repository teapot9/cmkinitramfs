# cmkinitramfs

A customizable initramfs generator made to support different kind of
configurations.

## cmkinitramfs

Build an initramfs from a configuration file.

First create the initramfs in a temporary directory.
The initramfs is then installed to `/usr/src/initramfs.cpio`. The temporary
directory used to build it is deleted. The kernel is cleaned from the
initramfs related files (only relevant for built-in initramfs). Then the
initramfs is copied and compressed to `/boot/initramfs.cpio.xz`.

Usage:
```
cmkinitramfs [--help] [--debug] [--dry-run] [--output OUTPUT] [kernel]
```
 * `--help`: Show command usage.
 * `--dry-run`: Only create the initramfs in a temporary directory, don't
   actually create the cpio archive or install it.
 * `--debug`: Same as dry-run but also don't create files which requires root
   privileges (e.g. `/dev/console`), this allows the program to be run as a
   normal user.
 * `--output`: Change the path of the created CPIO archive. Defaults to
   `/usr/src/initramfs.cpio`. If this is set, the initramfs *will not*
   be installed to `/boot`. Set to "-" to output the CPIO archive to stdout.
 * `--clean`: *Overwrite* the temporary directory to build initramfs.
 * `--quiet`: Don't output status informations to stderr.
 * `kernel`: Selects a specific kernel version, defaults to the current kernel
   in `/usr/src/linux`. This option is used to cleanup the initramfs from
   the kernel tree and force it's update (only for built-in initramfs).
   Set to "none" to disable the cleanup.

This script run `cmkinit`, the path of the executable can be overriden with
the `CMKINIT_SCRIPT` environment variable.

## cmkinit

Build the `/init` script according to the configuration file. This program is
used by cmkinitramfs, but can be called independently.

The content of the script is outputed to stdout.

## cmkinitramfs.py

Module providing functions used by *cmkinitramfs*. This library should
be the only code reading or writing to the temporary directory. All
modification to the initramfs directory should pass through it.

This module provides a global variable `DESTDIR` to select the path of the
initramfs temporary directory, it defaults to `/tmp/initramfs`.

The global variable `QUIET` is a boolean, if set to True, it will reduce
the information outputed to stderr.

## cmkinit.py

Module providing functions related to the construction of the `/init` script,
and classes allowing the management of data sources used during the boot
process.

Each data type in the configuration file has a corresponding Data class in
this module.

## cmkinitramfs.ini

This is the configuration file for both programs.

It contains one `DEFAULT` section and one section for each data source used
during the init process.

It's default path is `/etc/cmkinitramfs.ini` and can be overriden by the
`CMKINITCFG` environment variable.

### DEFAULT section

This section configures the programs behaviour.

It defines the temporary directory used to build the initramfs, the root
data source, the data sources required to boot, keyboard keymap, final init
process to run, special files needed to be copied, ...

The `/init` script will first initialize itself, then load the *root* data
source, then all the data sources enumerated in *mountpoints*.

### Section of any type

Any section other than DEFAULT is a data source.

Configuration found for multiple types:
 * type: Defines the type of data (e.g. luks, lvm, ...)
 * need: Defines data sources needed for this data source to be loaded and used.
 * load-need: Defines data sources needed for this data source to be loaded,
   but not needed to use it (load-time dependency). If a data source is only
   found in load-need, it will be unloaded when not needed anymore. For
   security purposes (e.g. LUKS key), load-time dependencies will load other
   data sources in order to be unloaded as soon as possible.
 * source: Define a data source to use as source for the current one (e.g. a
   device to mount or unlock). Multiple formats are accepted:
    * "PATH=/path/to/file": Pass a file path as source (e.g. /root/key or
      /dev/sda1).
    * "UUID=xxxx-yyyy": Pass an UUID as source, this will use the `findfs`
      program to find a corresponding device.
    * "DATA=data-source-name": Pass an other data source as source, data sources
      know how to find their corresponding data. e.g. my-luks-partition will
      return /dev/mapper/my-luks-partition-name.
    * Anything else: Use it as a data source name, "xxx" is the same as
      "DATA=xxx".

### Section type luks

Defines a LUKS data source.

Configuration (**bold** = mandatory):
 * **type**
 * **need**
 * **load-need**
 * **source**
 * **name**: Name to use for the luks device.
 * key: Defines a data source to use as LUKS key, same format as *source*.
 * header: Defines a data source to use as LUKS header, same format as *source*.

### Section type lvm

Defines a LVM logical volume.

Configuration (**bold** = mandatory):
 * **type**
 * **need**
 * **load-need**
 * **vg-name**: Defines the volume group name (e.g. my-vg).
 * **lv-name**: Defines the logical volume name (e.g. my-lv).

### Section type mount

Defines a mountpoint.

Configuration (**bold** = mandatory):
 * **type**
 * **need**
 * **load-need**
 * **source**
 * **mountpoint**: Defines the mountpoint where to mount the source.
   Only directories directly within `/mnt` will be created if they don't exists,
   any other mountpoint has to have an existing directory. e.g. /mnt/a/b will
   fail if *a* or *b* does not exists, but /mnt/a will create *a* if it does not
   exists.
 * **filesystem**: Defines the filesystem (e.g. ext4).
 * options: Mount options to use, defaults to "ro". If you want to use system
   defaults, put "defaults" here and not an empty string.

### Section type md

Defines a MD RAID device.

Configuration (**bold** = mandatory):
 * **type**
 * **need**
 * **load-need**
 * **name**: Name to use for the md device.
 * **source** or **sourceN**: Defines data sources to use for assembling the
   raid device. Use *source* when only one source is needed. Use *source0*,
   *source1*, ... when one or more sources are needed. The indexing must
   start at 0.

### Section type clone

Defines the cloning of a directory to another.

Configuration (**bold** = mandatory):
 * **type**
 * **need**
 * **load-need**
 * **source**
 * **destination**: Specify the destination of the clone, same format as
   *source*.

