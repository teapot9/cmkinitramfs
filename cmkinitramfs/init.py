"""Module providing functions to build an init script

The :func:`mkinit` function will generate a ``/init`` script.

``do_foo()`` functions write a string performing the foo action into a
stream. This stream should be the init script.

``_fun_foo()`` functions write a string defining the foo function into a
stream. This stream should be the init script.
"""

from __future__ import annotations

import itertools
import locale
from enum import Enum, auto
from shlex import quote
from typing import Iterable, IO, Optional, Tuple

from .data import _die, Data


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
        'if [ -d "/lib/modules/$(uname -r)" ]; then\n',
        '\tdepmod || ', _die('Failed to generate modules.dep'),
        'else\n',
        '\tprintk "WARNING: This initramfs may be incompatible with ',
        'the current kernel $(uname -r)"\n',
        'fi\n',
        "\n",
    ))


def do_cmdline(out: IO[str]) -> None:
    """Parse the kernel command line for known parameters

    Note: the command line is parsed up to "--", arguments after this
    are passed through to the final init process.

    Parsed parameters:

     - ``init=<path to init>``: Set the program to run as init process
       after the initramfs.
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
        "\tinit=*) INIT=\"${cmdline#*=}\" ;;\n"
        "\trd.break) RD_BREAK_ROOTFS=true ;;\n",
        "\trd.break=*)\n",
        "\t\tOLDIFS=\"${IFS}\"\n",
        "\t\tIFS=','\n",
        "\t\tfor bpoint in ${cmdline#*=}; do\n",
        "\t\t\tcase \"${bpoint}\" in\n",
        "\t\t\tinit) RD_BREAK_INIT=true ;;\n",
        "\t\t\tmodule) RD_BREAK_MODULE=true ;;\n",
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


def do_switch_root(out: IO[str], newroot: Data, init: str = '/sbin/init') \
        -> None:
    """Cleanup and switch root

      - Set kernel log level back to boot-time default
      - Unmount ``/dev``, ``/sys``, ``/proc``
      - Switch root

    :param out: Stream to write into
    :param newroot: Data to use as new root
    :param init: Init process to execute from the new root
    """
    out.writelines((
        '[ -z "${INIT+x}" ] && INIT=', quote(init), '\n',
        'printk "Run ${INIT} as init process"\n',
        'if [ -n "${RD_DEBUG+x}" ]; then\n',
        '\tprintk \'  with arguments:\'\n',
        '\tfor arg in "$@"; do printk "    ${arg}"; done\n',
        '\tprintk \'  with environment:\'\n',
        '\tenv | while read -r var; do printk "    ${var}"; done\n',
        'fi\n',

        "verb=\"$(awk '{ print $4 }' /proc/sys/kernel/printk)\"\n",
        'echo "${verb}" >/proc/sys/kernel/printk\n',
        "umount /dev || umount -l /dev || ", _die('Failed to unmount /dev'),
        "umount /proc || umount -l /proc || ", _die('Failed to unmount /proc'),
        "umount /sys || umount -l /sys || ", _die('Failed to unmount /sys'),
        "echo 'INITRAMFS: End'\n",
        'exec switch_root ', newroot.path(), ' "${INIT}" "$@" || ',
        _die('Failed to switch root'),
        "\n",
    ))


def mkinit(
        out: IO[str],
        root: Data,
        mounts: Iterable[Data] = (),
        keymap: Optional[str] = None,
        modules: Iterable[Tuple[str, Iterable[str]]] = (),
        ) -> None:  # noqa: E123
    """Create the init script

    :param out: Stream to write into
    :param root: :class:`Data` to use as rootfs
    :param mounts: :class:`Data` needed in addition of rootfs
    :param keymap: Path of the keymap to load, :data:`None` means no keymap
    :param modules: Kernel modules to be loaded in the initramfs:
        ``(module, (arg, ...))``. ``module`` is the module name string,
        and ``(arg, ...)``` is the iterable with the module parameters.
    """

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
    do_switch_root(out, root)
