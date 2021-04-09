============
cmkinitramfs
============

Tools to generate an initramfs from a configuration file.

About
=====

This project provides three main executables:
``cmkinit``, ``cmkcpiodir`` and ``cmkcpiolist``.

``cmkinit`` builds an init script from a configuration file.

``cmkcpiodir`` and ``cmkcpiolist`` build an initramfs,
including the init script, from the same configuration file.
``cmkcpiodir`` builds the initramfs into a directory on a filesystem,
and generates the CPIO archive from it.
``cmkcpiolist`` builds a CPIO list, using the same format as Linux kernel's
``gen_init_cpio`` utility, and generates the CPIO archive using
``gen_init_cpio``. See `the corresponding Linux kernel documentation`__
for more information.

.. __: https://www.kernel.org/doc/html/latest/filesystems/ramfs-rootfs-initramfs.html


Installation
============

Dependencies
------------

Python dependencies:

 - Documentation:

   - ``sphinx``
   - ``sphinx_rtd_theme``

Other dependencies:

 - mkramfs (mkcpiodir and mkcpiolist) dependencies:

   - ``loadkeys`` (kbd)
   - ``lddtree`` (pax-utils)
   - ``busybox`` (busybox)

 - mkcpiodir dependencies:

   - ``find`` (findutils, busybox)
   - ``cpio`` (cpio, busybox)

 - mkcpiolist dependencies:

   - ``gen_init_cpio`` (linux kernel)

Install
-------

Install from pypi:

.. code-block:: console

   $ pip install cmkinitramfs

Install from source with setup.py:

.. code-block:: console

   $ git clone https://github.com/teapot9/cmkinitramfs.git
   $ cd cmkinitramfs
   $ python3 setup.py install

Install from source with pip:

.. code-block:: console

   $ git clone https://github.com/teapot9/cmkinitramfs.git
   $ cd cmkinitramfs
   $ pip3 install .


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

 - ``root`` (mandatory): Data identifier for the data to use as new root.

 - ``mountpoints`` (mandatory): Comma separated list of data identifier
   to load in addition of rootfs. Can be empty.

 - ``keymap`` (optional): Boolean value defining if a keymap should be
   loaded. If set to ``no``, all ``keymap-*`` configurations will be ignored.
   Defaults to ``no``.

 - ``keymap-src`` (optional): Path of the keymap file to use. If not
   specified but ``keymap`` is ``yes``, the converted keymap should already
   exists at ``keymap-path``.

 - ``keymap-path`` (optional): Path where the binary keymap will be
   generated (generated from ``keymap-src``).
   Defaults to ``/tmp/keymap.bmap``.

 - ``keymap-dest`` (optional): Path of the keymap file within the initramfs.
   Defaults to ``/root/keymap.bmap``.

 - ``init`` (optional): Configure which init process to run at the end of
   the init script.
   Defaults to ``/sbin/init``.

 - ``init-path`` (optional): Path where the init script will be generated
   (generated from :func:`cmkinitramfs.mkinit.mkinit`).
   Defaults to ``/tmp/init.sh``.

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

 - ``cmkcpiodir-default-opts`` (optional): Options to append to the
   ``cmkcpiodir`` command line.

 - ``cmkcpiolist-default-opts`` (optional): Options to append to the
   ``cmkcpiolist`` command line.

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

cmkcpiodir
----------

.. code-block:: console

   $ cmkcpiodir --help
   usage: cmkcpiodir [-h] [--version] [--only-build-archive | --only-build-directory] [--debug] [--keep] [--clean] [--verbose] [--quiet] [--output OUTPUT] [--build-dir BUILD_DIR]

   Build an initramfs using a directory.

   optional arguments:
     -h, --help            show this help message and exit
     --version             show program's version number and exit
     --only-build-archive, -c
                           only build the CPIO archive from an existing initramfs directory
     --only-build-directory, -D
                           only build the initramfs directory, implies -k
     --debug, -d           debugging mode: non-root, implies -k
     --keep, -k            keep the created initramfs directory
     --clean, -C           overwrite temporary directory if it exists, use carefully
     --verbose, -v         be verbose
     --quiet, -q           be quiet (can be repeated)
     --output OUTPUT, -o OUTPUT
                           set the output of the CPIO archive
     --build-dir BUILD_DIR, -b BUILD_DIR
                           set the location of the initramfs directory

Running ``cmkcpiodir`` will generate the initramfs to a directory, then
it will create the CPIO archive from this directory.
``cmkcpiodir`` requires root privileges when run in non-debug mode,
see the ``do_nodes`` options of
:meth:`cmkinitramfs.mkramfs.Initramfs.build_to_directory`.

cmkcpiolist
-----------

.. code-block:: console

   $ cmkcpiolist --help
   usage: cmkcpiolist [-h] [--version] [--only-build-archive | --only-build-list] [--debug] [--keep] [--verbose] [--quiet] [--output OUTPUT] [--cpio-list CPIO_LIST]

   Build an initramfs using a CPIO list

   optional arguments:
     -h, --help            show this help message and exit
     --version             show program's version number and exit
     --only-build-archive, -c
                           only build the CPIO archive from an existing CPIO list
     --only-build-list, -L
                           only build the CPIO list, implies -k
     --debug, -d           debugging mode: non-root, implies -k
     --keep, -k            keep the created CPIO list
     --verbose, -v         be verbose
     --quiet, -q           be quiet (can be repeated)
     --output OUTPUT, -o OUTPUT
                           set the output of the CPIO archive (can be set in the configuration file)
     --cpio-list CPIO_LIST, -l CPIO_LIST
                           set the location of the CPIO list

Running ``cmkcpiolist`` will generate an initramfs CPIO list to a file,
then it will create the CPIO archive from this list with ``gen_init_cpio``.
``cmkcpiolist`` does not requires root privileges.


Examples
========

Command-line interface
----------------------

.. code-block:: console

   $ cmkcpiodir

..

 - Creates init script in ``/tmp/init.sh`` (set the path in the config file
   with ``init-path``).
 - If enabled, builds binary keymap in ``/tmp/keymap.bmap``.
 - Builds initramfs in ``/tmp/initramfs`` (disable this step with
   ``--only-build-archive``).
 - Builds CPIO archive from ``/tmp/initramfs`` to ``/usr/src/initramfs.cpio``
   (disable this step with ``--only-build-directory``).
 - Cleanup ``/tmp/initramfs`` directory (disable with ``--keep``).

.. code-block:: console

   $ cmkcpiolist

..

 - Creates init script in ``/tmp/init.sh`` (set the path in the config file
   with ``init-path``).
 - If enabled, builds binary keymap in ``/tmp/keymap.bmap``.
 - Builds CPIO list in ``/tmp/initramfs.list`` (disable this step with
   ``--only-build-archive``).
 - Builds CPIO archive from ``/tmp/initramfs.list``
   to ``/usr/src/initramfs.cpio`` (disable this step with
   ``--only-build-list``).

Configuration
-------------

.. literalinclude:: ../../config/cmkinitramfs.ini.example
   :language: ini
   :linenos:
