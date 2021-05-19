====
data
====

.. automodule:: cmkinitramfs.data
   :platform: Linux

.. autoclass:: Data
   :members: initialize, iter_all_deps,
      is_final, set_final, add_dep, add_load_dep,
      load, unload, path
   :private-members: _pre_load, _post_load, _pre_unload, _post_unload
   :special-members: __str__
   :show-inheritance:

.. autoclass:: PathData
   :show-inheritance:

.. autoclass:: UuidData
   :show-inheritance:

.. autoclass:: LabelData
   :show-inheritance:

.. autoclass:: LuksData
   :show-inheritance:

.. autoclass:: LvmData
   :private-members: __lvm_conf
   :show-inheritance:

.. autoclass:: MountData
   :private-members: __fun_fsck
   :show-inheritance:

.. autoclass:: MdData
   :show-inheritance:

.. autoclass:: CloneData
   :show-inheritance:

.. autoexception:: DataError
   :show-inheritance:

