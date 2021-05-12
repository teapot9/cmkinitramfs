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
from typing import Iterable, IO, Mapping, Optional, Tuple

from .data import _die, Data


#: Global Busybox applet dependencies
BUSYBOX_COMMON_DEPS = {
    '[', 'cat', 'cut', 'echo', 'env', 'exec', 'exit', 'export', 'mount',
    'set', 'switch_root', 'sync', 'test', 'umount', 'uname',
}
#: Keymap loading Busybox applet dependencies
BUSYBOX_KEYMAP_DEPS = {'loadkmap', 'kbd_mode'}
#: Kernel module loading Busybox applet dependencies
BUSYBOX_KMOD_DEPS = {'depmod', 'modprobe'}


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

    ``rescue_shell`` drop the user to ``/bin/sh``.

    Arguments: error message.

    This function *should not* be called from a subshell.

    This function *does not* return.

    :param out: Stream to write into
    """
    out.writelines((
        "rescue_shell()\n",
        "{\n",
        "\temerg \"$@\"\n",
        "\tnotice 'Dropping into a shell'\n",
        "\texec '/bin/sh'\n",
        "}\n\n",
    ))


def _fun_panic(out: IO[str]) -> None:
    """Define the panic function

    ``panic`` causes a kernel panic by exiting ``/init``.

    Arguments: error message.

    This function *should not* be called from a subshell.

    This function *does not* return.

    :param out: Stream to write into
    """
    out.writelines((
        "panic()\n",
        "{\n",
        "\temerg \"$@\"\n",
        "\tnotice 'Terminating init'\n",
        "\tsync\n",
        "\texit\n",
        "}\n\n",
    ))


def _fun_die(out: IO[str]) -> None:
    """Define the die function

    ``die`` will either start a rescue shell or cause a kernel panic,
    wether ``RD_PANIC`` is set or not.

    Arguments: error message passed to ``panic`` or ``rescue_shell``.

    This function *should not* be called from a subshell.

    This function *does not* return.

    :param out: Stream to write into
    """
    out.writelines((
        "die()\n",
        "{\n",
        "\tkill -TERM -1\n",
        "\t[ -n \"${RD_PANIC+x}\" ] && panic \"$@\" || rescue_shell \"$@\"\n",
        "}\n\n",
    ))


def _fun_log(out: IO[str]) -> None:
    """Define the logging functions

    ``log``: log a message.

     - Argument 1: syslog level number, from 0 to 7.
     - Additionnal arguments: message to log.

    Logs printed to stderr:

     - Level ≤ 4: always
     - 5 ≤ level ≤ 6: if debug enabled or quiet disabled
     - Level = 7: if debug enabled

    Helper functions:

     - ``emerg``: log a message for a panic condition.
       The message is prepended by 'FATAL:'.
     - ``alert``: log a critical error message requiring immediate action.
       The message is prepended by 'ERROR:'.
     - ``crit``: log a critical error message.
       The message is prepended by 'ERROR:'.
     - ``err``: log an error message.
       The message is prepended by 'ERROR:'.
     - ``warn``: log a warning message.
       The message is prepended by 'WARNING:'.
     - ``notice``: log a significant/unusual informational message.
     - ``info``: log an informational message.
     - ``debug``: log a debug-level message.

    Helper functions will call ``log`` with the coresponding syslog level.

    Logging functions always return successfully.

    :param out: Stream to write into
    """
    out.writelines((
        'log()\n',
        '{\n',
        '\t[ "${1-}" -lt 8 ] && lvl="$1" && shift || lvl=1\n',
        '\t[ $# -ge 1 ] || return 0\n',
        '\techo "<$((24 | lvl))>initramfs:" "$@" 1>/dev/kmsg\n',
        '\tif [ "${lvl}" -eq 5 ] || [ "${lvl}" -eq 6 ] ',
        '&& [ -z "${RD_QUIET+x}" ] || [ -n "${RD_DEBUG+x}" ] ',
        '|| [ "${lvl}" -le 4 ]\n',
        '\tthen echo "$@" 1>&2\n',
        '\tfi\n',
        '\treturn 0\n',
        '}\n',
        '\n',
        'emerg() { log 0 \'FATAL:\' "$@" ; }\n',
        'alert() { log 1 \'ERROR:\' "$@" ; }\n',
        'crit() { log 2 \'ERROR:\' "$@" ; }\n',
        'err() { log 3 \'ERROR:\' "$@" ; }\n',
        'warn() { log 4 \'ERROR:\' "$@" ; }\n',
        'notice() { log 5 "$@" ; }\n',
        'info() { log 6 "$@" ; }\n',
        'debug() { log 7 "$@" ; }\n',
        '\n',
    ))


def do_header(out: IO[str], home: str = '/root', path: str = '/bin:/sbin') \
        -> None:
    """Create the /init header

     - Create the shebang ``/bin/sh``
     - Configure environment variables
     - Define global functions (``panic``, logging, ...)

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
    _fun_panic(out)
    _fun_die(out)
    _fun_log(out)


def do_init(out: IO[str]) -> None:
    """Initialize the init environment

     - Check the current PID is 1
     - Mount ``/proc``, ``/sys``, ``/dev``
     - Set the kernel log level to 3

    :param out: Stream to write into
    """
    out.writelines((
        "debug 'Initialization'\n",
        "test $$ -eq 1 || ",
        _die('init expects to be run as PID 1'),
        "mount -t proc none /proc || ",
        _die('Failed to mount /proc'),
        "mount -t sysfs none /sys || ",
        _die('Failed to mount /sys'),
        "mount -t devtmpfs none /dev || ",
        _die('Failed to mount /dev'),
        "echo 3 1>'/proc/sys/kernel/printk'\n",
        '[ ! -d "/lib/modules/$(uname -r)" ] || depmod || ',
        _die('Failed to generate modules.dep'),
        "\n",
    ))


def do_cmdline(out: IO[str]) -> None:
    """Parse the kernel command line for known parameters

    Note: the command line is parsed up to "--", arguments after this
    are passed through to the final init process.

    Parsed parameters:

     - ``init=<path to init>``: Set the program to run as init process
       after the initramfs.
     - ``debug``: Enable debugging, see ``rd.debug``.
     - ``quiet``: Enable quiet mode, see ``rd.quiet``.
     - ``rd.break={init|rootfs|mount}``: Stops the boot process,
       defaults to ``rootfs``. See :class:`Breakpoint`.
     - ``rd.debug``: Enable debugging mode: output verbose informations.
       If quiet mode is disabled, enable shell trace (with ``set -x``).
     - ``rd.panic``: On fatal error: cause a kernel panic rather than
       dropping into a shell.
     - ``rd.quiet``: Enable quiet mode: reduce verbosity.

    :param out: Stream to write into
    """
    out.writelines((
        "debug 'Parsing command-line'\n",
        "for cmdline in $(cat /proc/cmdline); do\n",
        "\tcase \"${cmdline}\" in\n",
        "\t--) break ;;\n",
        "\tinit=*) INIT=\"${cmdline#*=}\" ;;\n"
        "\tdebug) RD_DEBUG=true ;;\n",
        "\tquiet) RD_QUIET=true ;;\n",
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
        "\t\t\t*) err \"Unknown breakpoint ${bpoint}\" ;;\n",
        "\t\t\tesac\n",
        "\t\tdone\n",
        "\t\tIFS=\"${OLDIFS}\"\n",
        "\t\t;;\n",
        "\trd.debug) RD_DEBUG=true ;;\n",
        "\trd.panic) RD_PANIC=true ;;\n",
        "\trd.quiet) RD_QUIET=true ;;\n",
        "\t*) unknown_cmd=\"${unknown_cmd-}${unknown_cmd+ }${cmdline}\" ;;\n",
        "\tesac\n",
        "done\n",
        "\n",
        "[ -n \"${RD_DEBUG+x}\" ] && [ -z \"${RD_QUIET+x}\" ] ",
        "&& PS4='+ $0:$LINENO: ' && set -x\n",
        "[ -n \"${unknown_cmd+x}\" ] ",
        "&& debug \"Skipped unknown cmdlines: ${unknown_cmd}\"\n",
        "unset unknown_cmd\n",
        "\n",
    ))


def do_keymap(out: IO[str], keymap_file: str, unicode: bool = True) -> None:
    """Load a keymap

    :param out: Stream to write into
    :param keymap_file: Absolute path of the file to load
    :param unicode: Set the keyboard in unicode mode (rather than ASCII)
    """
    out.writelines((
        "info 'Loading keymap'\n",
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
        f"info 'Loading kernel module {module}'\n",
        f"modprobe {quote(module)} ", *quoted_args, '|| ',
        _die(f'Failed to load module {module}'),
        '\n',
    ))


def do_break(out: IO[str], breakpoint_: Breakpoint,
             scripts: Iterable[str] = ()) -> None:
    """Drop into a shell if rd.break is set

    :param out: Stream to write into
    :param breakpoint_: Which breakpoint to check
    :param scripts: User commands to run before the breakpoint
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

    if scripts:
        out.write(f"info 'Running user scripts for {breakpoint_}'\n")
        for script in scripts:
            out.writelines((script, "\n"))
        out.write("\n")
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
        'info "Run ${INIT} as init process"\n',
        'debug \'  with arguments:\'\n',
        'for arg in "${INIT}" "$@"; do debug "    ${arg}"; done\n',
        'debug \'  with environment:\'\n',
        'OLDIFS="${IFS}"\n',
        'IFS="$(printf \'\\n\\b\')"\n',
        'for var in $(env); do debug "    ${var}"; done\n',
        'IFS="${OLDIFS}"\n',
        '\n',
        'verb="$(cut -d"$(printf \'\\t\')" -f4 -s /proc/sys/kernel/printk)"\n',
        'echo "${verb}" >/proc/sys/kernel/printk\n',
        'kill -TERM -1\n',
        "umount /dev || umount -l /dev || ", _die('Failed to unmount /dev'),
        "umount /proc || umount -l /proc || ", _die('Failed to unmount /proc'),
        "umount /sys || umount -l /sys || ", _die('Failed to unmount /sys'),
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
        scripts: Optional[Mapping[Breakpoint, Iterable[str]]] = None,
        ) -> None:  # noqa: E123
    """Create the init script

    :param out: Stream to write into
    :param root: :class:`Data` to use as rootfs
    :param mounts: :class:`Data` needed in addition of rootfs
    :param keymap: Path of the keymap to load, :data:`None` means no keymap
    :param modules: Kernel modules to be loaded in the initramfs:
        ``(module, (arg, ...))``. ``module`` is the module name string,
        and ``(arg, ...)``` is the iterable with the module parameters.
    :param scripts: User commands to run. ``{breakpoint: commands}``:
        ``breakpoint`` is the :class:`Breakpoint` where the commands will
        be run. ``commands`` is the iterable with the commands.
    """

    datatypes = set()
    for data in itertools.chain((root,), mounts):
        datatypes.add(type(data))
        for dep in data.iter_all_deps():
            datatypes.add(type(dep))
    if scripts is None:
        scripts = {}

    do_header(out)
    do_break(out, Breakpoint.EARLY, scripts.get(Breakpoint.EARLY, ()))
    do_init(out)
    for datatype in datatypes:
        datatype.initialize(out)
    do_cmdline(out)
    if keymap is not None:
        do_keymap(out, keymap,
                  unicode=(locale.getdefaultlocale()[1] == 'UTF-8'))
    do_break(out, Breakpoint.INIT, scripts.get(Breakpoint.INIT, ()))
    for mod_name, mod_args in modules:
        do_module(out, mod_name, *mod_args)
    do_break(out, Breakpoint.MODULE, scripts.get(Breakpoint.MODULE, ()))
    root.load(out)
    do_break(out, Breakpoint.ROOTFS, scripts.get(Breakpoint.ROOTFS, ()))
    for mount in mounts:
        mount.load(out)
    do_break(out, Breakpoint.MOUNT, scripts.get(Breakpoint.MOUNT, ()))
    do_switch_root(out, root)
