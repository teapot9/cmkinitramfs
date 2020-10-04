"""setuptools based setup module"""

import setuptools

with open("README.md", "r") as readme:
    long_description = readme.read()

setuptools.setup(
    name="cmkinitramfs",
    version="0.1.0",
    description="A customizable simple initramfs generator",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/lleseur/cmkinitramfs",
    author="lleseur",
    classifiers=[
        "License :: OSI Approved :: MIT License",
        "Programming Language :: Python :: 3",
        "Operating System :: POSIX :: Linux",
    ],
    packages=['cmkinitramfs'],
    python_requires=">=3.6",
    data_files=[(
        "share/cmkinitramfs",
        ["cmkinitramfs.ini.default", "cmkinitramfs.ini.example"]
    )],
    entry_points={
        'console_scripts': [
            'cmkinit = cmkinitramfs.mkinit:entry_point',
            'cmkinitramfs = cmkinitramfs.mkramfs:entry_point',
        ],
    },
    install_requires=[
        'pyelftools',
    ],
)

