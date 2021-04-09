=======
mkramfs
=======

.. automodule:: cmkinitramfs.mkramfs
   :platform: Linux

.. autofunction:: findlib

.. autofunction:: findexec

.. autofunction:: find_elf_deps

.. autofunction:: busybox_get_applets

.. autofunction:: mkcpio_from_dir

.. autofunction:: mkcpio_from_list

.. autofunction:: hash_file

.. autoexception:: MergeError
   :show-inheritance:

.. autoclass:: Item
   :members: merge, build_to_cpio_list, build_to_directory
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
   :members: add_item, add_file, build_to_cpio_list, build_to_directory
   :show-inheritance:

.. autofunction:: mkinitramfs

.. autofunction:: keymap_build

