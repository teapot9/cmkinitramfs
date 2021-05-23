============
cmkinitramfs
============

|github version badge|
|pypi version badge|
|qa badge|
|doc badge|
|py version badge|
|py implementation badge|

Tools to generate an initramfs from a configuration file.

Documentation is available at https://cmkinitramfs.readthedocs.io/.

.. |github version badge| image:: https://badge.fury.io/gh/teapot9%2Fcmkinitramfs.svg
   :target: https://github.com/teapot9/cmkinitramfs
   :alt: Github repository

.. |pypi version badge| image:: https://badge.fury.io/py/cmkinitramfs.svg
   :target: https://pypi.org/project/cmkinitramfs/
   :alt: PyPI package

.. |qa badge| image:: https://github.com/teapot9/cmkinitramfs/actions/workflows/qa.yml/badge.svg
   :target: https://github.com/teapot9/cmkinitramfs/actions/workflows/qa.yml
   :alt: Quality assurance

.. |doc badge| image:: https://readthedocs.org/projects/cmkinitramfs/badge/?version=latest
   :target: https://cmkinitramfs.readthedocs.io/en/latest/
   :alt: Documentation status

.. |py version badge| image:: https://img.shields.io/pypi/pyversions/cmkinitramfs.svg
   :alt: Python version

.. |py implementation badge| image:: https://img.shields.io/pypi/implementation/cmkinitramfs.svg
   :alt: Python implementation

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

Compatibility
-------------

Python version: this library is compatible with
**Python ≥ 3.7**.

Python implementation: this library is compatible with
**CPython** and **PyPy**.

Dependencies
------------

Python dependencies:

 - bin (mkcpiodir and mkcpiolist) dependencies:

   - ``pyelftools``

 - Documentation:

   - ``sphinx``
   - ``sphinx_rtd_theme``

 - Tests:

   - QA:

     - ``flake8``
     - ``mypy``
     - ``tox``

Other dependencies:

 - initramfs (mkcpiodir and mkcpiolist) dependencies:

   - ``loadkeys`` (kbd)
   - ``busybox``
   - ``modinfo`` (kmod, busybox)

 - mkcpiodir dependencies:

   - ``find`` (findutils, busybox)
   - ``cpio`` (cpio, busybox)

 - mkcpiolist dependencies:

   - ``gen_init_cpio`` (linux kernel, linux-misc-apps)

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

Some options expects a data source as input, there are several data identifier
formats:

 - ``DATA=data-name``: data defined in the section with the same name.
 - ``data-name``: same as ``DATA=data-name``.
 - ``PATH=/path/foo/bar``: data at the path ``/path/foo/bar``, this can
   be a directory, a file, or a block device.
 - ``/absolute/path``: same as ``PATH=/absolute/path``.
 - ``UUID=1234-5678``: filesystem with UUID ``1234-5678``.
 - ``LABEL=foo``: filesystem with label ``foo``.
 - ``PARTUUID=1234-5678``: partition with UUID ``1234-5678``.
 - ``PARTLABEL=foo``: partition with label ``foo``.

DEFAULT section
---------------

This section has default values for other sections, as well as
global configuration.

 - ``root`` (mandatory): Data identifier for the data to use as new root.

 - ``mountpoints`` (optional): Comma separated list of data identifier
   to load in addition of rootfs.

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

 - ``init-path`` (optional): Path where the init script will be generated
   (generated from ``cmkinitramfs.init.mkinit()``).
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
   in directories from ``/etc/ld.so.conf`` and the ``LD_LIBRARY_PATH``
   environment variable.

 - ``busybox`` (optional): Additional executables to include in the initramfs.
   Each item is separated by a newline. Format: ``exec``:
   name of the command (basename).
   If busybox provides the command, they will not be added. Otherwise,
   the executable is searched in ``PATH``.

 - ``cmkcpiodir-default-opts`` (optional): Options to append to the
   ``cmkcpiodir`` command line.

 - ``cmkcpiolist-default-opts`` (optional): Options to append to the
   ``cmkcpiolist`` command line.

 - ``modules`` (optional): Kernel modules to load in the initramfs.
   One module per line, each line with the module name followed by the
   module parameters (e.g. ``mymodule foo=bar``).

 - ``scripts`` (optional): User scripts to run at a given breakpoint.
   One user script per line with the format ``breakpoint:script``.
   The script ``script`` will be run at the breakpoint ``breakpoint``.
   A list of available breakpoints is available in
   ``cmkinitramfs.init.Breakpoint``.
   These scripts will be run wether the breakpoint is enabled or not.
   Example: ``init: ls /dev``: run ``ls /dev`` after initialization.

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

.. |need| replace:: ``need`` (optional): Hard dependencies: comma separated
   list of data identifiers. Those dependencies are required to load
   *and* use the data.

.. |load-need| replace:: ``load-need`` (optional): Load dependencies: comma
   separated list of data identifiers. Those dependencies are only required
   to load the data, they can be unloaded when the data has been successfully
   loaded. (e.g. A LUKS key, an archive to decompress.)


Usage
=====

Kernel command-line parameters
------------------------------

The init script will check the kernel cmdline for known parameters.

 - ``debug``: Same as ``rd.debug``.
 - ``init=<path to init>``: Set the init process to run after the initramfs.
 - ``quiet``: Same as ``rd.quiet``.
 - ``rd.break=<breakpoint>``: Drop into a shell at a given point.
   See ``cmkinitramfs.init.Breakpoint``.
 - ``rd.debug``: Show debugging informations.
 - ``rd.panic``: On fatal error: cause a kernel panic rather than droping
   into a shell.
 - ``rd.quiet``: Reduce log shown on console.

For more details, see ``cmkinitramfs.init.do_cmdline``.

cmkinit
-------

.. code-block:: console

   $ cmkinit --help
   usage: cmkinit [-h] [--version]

   Build an init script

   optional arguments:
     -h, --help  show this help message and exit
     --version   show program's version number and exit

Running ``cmkinit`` will generate an init script and output it to stdout.
No options are available, everything is defined in the configuration file.
The ``CMKINITCFG`` environment variable may be defined to use a custom
configuration file.

cmkcpiodir
----------

.. code-block:: console

   $ cmkcpiodir --help
   usage: cmkcpiodir [-h] [--version] [--debug] [--verbose] [--quiet]
                     [--output OUTPUT] [--binroot BINROOT] [--kernel KERNEL]
                     [--only-build-archive | --only-build-directory] [--keep]
                     [--clean] [--build-dir BUILD_DIR]

   Build an initramfs using a directory.

   optional arguments:
     -h, --help            show this help message and exit
     --version             show program's version number and exit
     --debug, -d           debugging mode: non-root, implies -k
     --verbose, -v         be verbose
     --quiet, -q           be quiet (can be repeated)
     --output OUTPUT, -o OUTPUT
                           set the output of the CPIO archive
     --binroot BINROOT, -r BINROOT
                           set the root directory for binaries (executables and
                           libraries)
     --kernel KERNEL, -K KERNEL
                           set the target kernel version of the initramfs,
                           defaults to the running kernel
     --only-build-archive, -c
                           only build the CPIO archive from an existing initramfs
                           directory
     --only-build-directory, -D
                           only build the initramfs directory, implies -k
     --keep, -k            keep the created initramfs directory
     --clean, -C           overwrite temporary directory if it exists, use
                           carefully
     --build-dir BUILD_DIR, -b BUILD_DIR
                           set the location of the initramfs directory

Running ``cmkcpiodir`` will generate the initramfs in a directory, then
it will create the CPIO archive from this directory.
``cmkcpiodir`` requires root privileges when run in non-debug mode,
see the ``do_nodes`` options of
``cmkinitramfs.initramfs.Initramfs.build_to_directory()``.

cmkcpiolist
-----------

.. code-block:: console

   $ cmkcpiolist --help
   usage: cmkcpiolist [-h] [--version] [--debug] [--verbose] [--quiet]
                      [--output OUTPUT] [--binroot BINROOT] [--kernel KERNEL]
                      [--only-build-archive | --only-build-list] [--keep]
                      [--cpio-list CPIO_LIST]

   Build an initramfs using a CPIO list

   optional arguments:
     -h, --help            show this help message and exit
     --version             show program's version number and exit
     --debug, -d           debugging mode: non-root, implies -k
     --verbose, -v         be verbose
     --quiet, -q           be quiet (can be repeated)
     --output OUTPUT, -o OUTPUT
                           set the output of the CPIO archive
     --binroot BINROOT, -r BINROOT
                           set the root directory for binaries (executables and
                           libraries)
     --kernel KERNEL, -K KERNEL
                           set the target kernel version of the initramfs,
                           defaults to the running kernel
     --only-build-archive, -c
                           only build the CPIO archive from an existing CPIO list
     --only-build-list, -L
                           only build the CPIO list, implies -k
     --keep, -k            keep the created CPIO list
     --cpio-list CPIO_LIST, -l CPIO_LIST
                           set the location of the CPIO list

Running ``cmkcpiolist`` will generate an initramfs CPIO list in a file,
then it will create the CPIO archive from this list with ``gen_init_cpio``.
``cmkcpiolist`` does not require root privileges.

findlib
-------

.. code-block:: console

   $ findlib --help
   usage: findlib [-h] [--verbose] [--quiet] [--version]
                  [--compatible COMPATIBLE] [--root ROOT] [--null] [--glob]
                  LIB [LIB ...]

   Find a library on the system

   positional arguments:
     LIB                   library to search

   optional arguments:
     -h, --help            show this help message and exit
     --verbose, -v         be verbose
     --quiet, -q           be quiet (can be repeated)
     --version             show program's version number and exit
     --compatible COMPATIBLE, -c COMPATIBLE
                           set a binary the library must be compatible with
     --root ROOT, -r ROOT  set the root directory to search for the library
     --null, -0            paths will be delemited by null characters instead of
                           newlines
     --glob, -g            library names are glob patterns

``findlib`` will search the absolute path of a library on the system.
It will search in directories from ``/etc/ld.so.conf``, ``LD_LIBRARY_PATH``,
and default library paths (see ``cmkinitramfs.bin.find_lib()`` and
``cmkinitramfs.bin.find_lib_iter()``).


Examples
========

Command-line interface
----------------------

.. code-block:: console

   $ cmkcpiodir

..

 - Creates init script in ``/tmp/init.sh``.
 - If enabled, builds binary keymap in ``/tmp/keymap.bmap``.
 - Builds initramfs in ``/tmp/initramfs`` (disable this step with
   ``--only-build-archive``).
 - Builds CPIO archive from ``/tmp/initramfs`` to ``/usr/src/initramfs.cpio``
   (disable this step with ``--only-build-directory``).
 - Cleanup ``/tmp/initramfs`` directory (disable with ``--keep``).

.. code-block:: console

   $ cmkcpiolist

..

 - Creates init script in ``/tmp/init.sh``.
 - If enabled, builds binary keymap in ``/tmp/keymap.bmap``.
 - Builds CPIO list in ``/tmp/initramfs.list`` (disable this step with
   ``--only-build-archive``).
 - Builds CPIO archive from ``/tmp/initramfs.list``
   to ``/usr/src/initramfs.cpio`` (disable this step with
   ``--only-build-list``).

.. code-block:: console

   $ findlib 'libgcc_s.so.1'
   /usr/lib/gcc/x86_64-pc-linux-gnu/10.2.0/libgcc_s.so.1

..

 - Searches the ``libgcc_s.so.1`` library on the system and prints it
   to stdout.

.. code-block:: console

   $ findlib -g 'libgcc_s.*'
   /usr/lib/gcc/x86_64-pc-linux-gnu/10.2.0/libgcc_s.so.1
   /lib64/libgcc_s.so.1
   /lib64/libgcc_s.so.1

..

 - Search any library matching ``libgcc_s.*`` on the system and prints them
   to stdout.

