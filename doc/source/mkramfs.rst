=======
mkramfs
=======

.. automodule:: cmkinitramfs.mkramfs
   :platform: Linux

.. autoclass:: ELFIncompatibleError
   :show-inheritance:

.. autofunction:: normpath

.. autofunction:: parse_ld_path

.. autofunction:: parse_ld_so_conf_iter

.. autofunction:: parse_ld_so_conf_tuple

.. autofunction:: _get_default_libdirs

.. autofunction:: _get_libdir

.. autofunction:: _is_elf_compatible

.. autofunction:: _get_elf_arch

.. autofunction:: _find_elf_deps_iter

.. autofunction:: find_elf_deps_iter

.. autofunction:: find_elf_deps_set

.. autofunction:: findlib

.. autofunction:: parse_path

.. autofunction:: findexec

.. autofunction:: _get_all_kmods

.. autofunction:: find_kmod

.. autofunction:: busybox_get_applets

.. autofunction:: mkcpio_from_dir

.. autofunction:: mkcpio_from_list

.. autofunction:: hash_file

.. autoexception:: MergeError
   :show-inheritance:

.. autoclass:: Item
   :members: is_mergeable, merge, build_from_cpio_list,
      build_to_cpio_list, build_to_directory
   :special-members: __iter__, __contains__
   :show-inheritance:

.. autoclass:: File
   :show-inheritance:

.. autoclass:: Directory
   :show-inheritance:

.. autoclass:: Node
   :show-inheritance:

.. autoclass:: Symlink
   :show-inheritance:

.. autoclass:: Pipe
   :show-inheritance:

.. autoclass:: Socket
   :show-inheritance:

.. autoclass:: Initramfs
   :members: add_item, mkdir, add_file, build_to_cpio_list, build_to_directory
   :special-members: __iter__, __contains__
   :show-inheritance:

.. autofunction:: mkinitramfs

.. autofunction:: keymap_build

