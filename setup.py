"""A customizable simple initramfs generator"""

import setuptools

from cmkinitramfs import (__author__, __doc__, __email__, __license__,
                          __name__, __url__, __version__)

setuptools.setup(
    name=__name__,
    version=__version__,
    description=__doc__,
    long_description=open('README.rst', 'r').read(),
    long_description_content_type='text/x-rst',

    author=__author__,
    author_email=__email__,
    license=__license__,
    url=__url__,

    python_requires='>=3.6, <4',
    install_requires=[],
    extras_require={
        'doc': ['sphinx', 'sphinx_rtd_theme'],
    },

    packages=['cmkinitramfs'],
    entry_points={
        'console_scripts': [
            'cmkinit = cmkinitramfs.entry:entry_cmkinit',
            'cmkcpiodir = cmkinitramfs.entry:entry_cmkcpiodir',
            'cmkcpiolist = cmkinitramfs.entry:entry_cmkcpiolist',
        ],
    },

    classifiers=[
        "Development Status :: 3 - Alpha",
        "Environment :: Console",
        "Intended Audience :: System Administrators",
        "License :: OSI Approved :: MIT License",
        "Operating System :: POSIX :: Linux",
        "Programming Language :: Python",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.7",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: Implementation :: CPython",
        "Programming Language :: Python :: Implementation :: PyPy",
        "Topic :: System :: Boot",
        "Topic :: System :: Boot :: Init",
        "Topic :: Utilities",
    ],
    keywords=['initramfs', 'initramfs-generator'],
)
