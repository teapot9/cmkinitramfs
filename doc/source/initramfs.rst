=========
initramfs
=========

.. automodule:: cmkinitramfs.initramfs
   :platform: Linux

.. autofunction:: busybox_get_applets

.. autofunction:: mkcpio_from_dir

.. autofunction:: mkcpio_from_list

.. autofunction:: keymap_build

.. autodata:: SHELL_SPECIAL_BUILTIN

.. autodata:: SHELL_RESERVED_WORDS

.. autoclass:: Initramfs
   :members: add_item, mkdir, add_file, add_library, add_executable, add_kmod,
      add_busybox, build_to_cpio_list, build_to_directory
   :special-members: __iter__, __contains__
   :private-members: __normalize
   :show-inheritance:

