============
cmkinitramfs
============

Tools to generate an initramfs from a configuration file.

About
=====

This project provides two main executables: ``cmkinit`` and ``cmkinitramfs``.

``cmkinit`` builds an init script from a configuration file.

``cmkinitramfs`` builds an initramfs, including the init script, from
the same configuration file.


Configuration
=============

The configuration file is in an *ini* format.

Each section defines a data source, the section name is the data identifier.

Some options expects a data source as input, there are 4 data identifier
formats:

 - ``data-name``: data defined in the section with the same name.
 - ``DATA=data-name``: same as ``data-name``.
 - ``PATH=/path/foo/bar``: data at the path ``/path/foo/bar``, this can
   be a directory, or a block device.
 - ``UUID=8490-47b4``: filesystem with UUID ``8490-47b4``.

DEFAULT section
---------------

This section has default values for other sections, as well as
global configuration.

 - ``build-dir`` (optional): Define the directory used to build the
   initramfs. Defaults to ``/tmp/initramfs``.
   See :data:`cmkinitramfs.mkramfs.DESTDIR`.

 - ``output`` (optional): Define the output file of the CPIO archive.
   Defaults to ``/usr/src/initramfs.cpio``. ``-`` will output to
   standard output.

 - ``root`` (mandatory): Data identifier for the data to use as new root.

 - ``mountpoints`` (mandatory): Comma separated list of data identifier
   to load in addition of rootfs. Can be empty.

 - ``keymap`` (optional): Path of the keymap file to use.

 - ``keymap-file`` (optional): Path of the keymap file within the initramfs,
   defaults to ``/root/keymap.bmap``.

 - ``init`` (optional): Configure which init process to run at the end of
   the init script.

 - ``files`` (optional): Additional files to include in the initramfs.
   Each item is separated by a newline. Format: ``source:destination``
   (e.g. ``files = /root/foo:/root/bar`` copy the file ``foo`` in the initramfs
   renaming it ``bar``). If no destination is given, the file will be copied
   to the same path as ``source`` in the initramfs. ``source`` can be an
   absolute or relative path, ``destination`` must be an absolute path
   within the initramfs.

 - ``execs`` (optional): Additional executables to include in the initramfs.
   Same format as ``files``, except that ``source`` will also be searched
   in directories from the ``PATH`` environment variable.

 - ``libs`` (optional): Additional libraries to include in the initramfs.
   Same format as ``files``, except that ``source`` will also be searched
   in directories from ``/etc/ld.so.conf``, ``/etc/ld.so.conf.d/*.conf``,
   and the ``LD_LIBRARY_PATH`` environment variable.

LUKS data sections
------------------

LUKS device to open.

 - ``type = luks`` (mandatory).

 - |need|

 - |load-need|

 - ``source`` (mandatory): Data identifier of the data to unlock.

 - ``name`` (mandatory): Name to use for the luks device, this will be
   used by cryptsetup.

 - ``key`` (optional): Data identifier for the LUKS key.

 - ``header`` (optional): Data identifier for the LUKS header.

 - ``discard`` (optional): Enable discards. Boolean value (yes/no).

LVM data sections
-----------------

LVM logical volume to load.

 - ``type = lvm`` (mandatory).

 - |need|

 - |load-need|

 - ``vg-name`` (mandatory): Volume group name.

 - ``lv-name`` (mandatory): Logical volume name.

Mount data sections
-------------------

Filesystem to mount.

 - ``type = mount`` (mandatory).

 - |need|

 - |load-need|

 - ``source`` (optional): Data identifier for the filesystem to mount.
   If not set, it will set the source to "none" (e.g. for TMPFS).

 - ``mountpoint`` (mandatory): Path where the filesystem will be mounted.

 - ``filesystem`` (mandatory): Which filesystem to use, option passed
   to ``mount -t filesystem``.

 - ``options`` (optional): Mount options, defaults to ``ro``.

MD data sections
----------------

MD RAID data to load.

 - ``type = md`` (mandatory).

 - |need|

 - |load-need|

 - ``name`` (mandatory): Name of the MD RAID, this will be used by mdadm.

 - ``source`` (mandatory): New line separated data identifiers of the
   sources to use. Multiple block devices can be specified, or the
   UUID of the MD RAID.

Clone data sections
-------------------

Clone a source to a destination.

 - ``type = clone`` (mandatory).

 - |need|

 - |load-need|

 - ``source`` (mandatory): Data identifier for the source of the clone.

 - ``destination`` (mandatory): Data identifier of the destination
   of the clone.

.. |need| replace:: ``need`` (mandatory): Hard dependencies: comma separated
   list of data identifiers. Those dependencies are required to load
   *and* use the data. Can be empty.

.. |load-need| replace:: ``load-need`` (mandatory): Load dependencies: comma
   separated list of data identifiers. Those dependencies are only required
   to load the data, they can be unloaded when the data has been successfully
   loaded. (e.g. A LUKS key, an archive to decompress.) Can be empty.


Usage
=====

cmkinit
-------

Running ``cmkinit`` will generate an init script and output it to stdout.
No options are available, everything is defined in the configuration file.
The ``CMKINITCFG`` environment variable may be defined to use a custom
configuration path.

cmkinitramfs
------------

.. code-block:: console

   $ cmkinitramfs --help
   usage: cmkinitramfs [-h] [--debug] [--output OUTPUT] [--clean] [--verbose] [--quiet]
   
   Build an initramfs.
   
   optional arguments:
     -h, --help            show this help message and exit
     --debug, -d           debugging mode: non-root, does not cleanup the build directory
     --output OUTPUT, -o OUTPUT
                           set output cpio file (can be set in the config file)
     --clean, -C           overwrite temporary directory if it exists, use carefully
     --verbose, -v         be verbose
     --quiet, -q           be quiet (can be repeated)

Running ``cmkinitramfs`` will generate the initramfs to the configured output.
``cmkinitramfs`` requires root privileges when run in non-debug mode,
see the ``debug`` option of :func:`cmkinitramfs.mkramfs.mklayout`.


Example configuration
=====================

.. literalinclude:: ../../config/cmkinitramfs.ini.example
   :language: ini
   :linenos:

