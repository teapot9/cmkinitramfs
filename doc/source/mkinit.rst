======
mkinit
======

.. automodule:: cmkinitramfs.mkinit
   :platform: Linux

.. autoclass:: Breakpoint
   :members:
   :show-inheritance:

.. autofunction:: _fun_rescue_shell

.. autofunction:: _fun_printk

.. autofunction:: _fun_panic

.. autofunction:: _fun_die

.. autofunction:: _die

.. autofunction:: mkinit

.. autofunction:: do_header

.. autofunction:: do_init

.. autofunction:: do_cmdline

.. autofunction:: do_keymap

.. autofunction:: do_break

.. autofunction:: do_switch_root

.. autoclass:: Data
   :members: deps_files, deps_execs, deps_libs, is_final, set_final,
        add_dep, add_load_dep, load, unload, path
   :private-members: _pre_load, _post_load, _pre_unload, _post_unload
   :special-members: __str__
   :show-inheritance:

.. autoclass:: PathData
   :show-inheritance:

.. autoclass:: UuidData
   :show-inheritance:

.. autoclass:: LuksData
   :show-inheritance:

.. autoclass:: LvmData
   :show-inheritance:

.. autoclass:: MountData
   :show-inheritance:

.. autoclass:: MdData
   :show-inheritance:

.. autoclass:: CloneData
   :show-inheritance:

.. autoexception:: DataError
   :show-inheritance:

