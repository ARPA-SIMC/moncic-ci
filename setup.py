#!/usr/bin/env python3
# coding: utf-8

import ast
from setuptools import setup
from typing import Optional

# Read VERSION from monci executable
version = None


def read_version(fname: str) -> Optional[str]:
    version = None
    with open(fname, "rt") as fd:
        tree = ast.parse(fd.read(), fname)
        for stm in tree.body:
            if version is not None:
                break
            if not isinstance(stm, ast.Assign):
                continue
            for target in stm.targets:
                if not isinstance(target, ast.Name):
                    continue
                if target.id != "VERSION":
                    continue
                if isinstance(stm.value, ast.Constant):
                    version = stm.value.value
                    break
                elif isinstance(stm.value, ast.Str):
                    version = stm.value.s
                    break
    if version is None:
        raise RuntimeError(f"VERSION not found in {fname}")
    return version


setup(
    name='moncic-ci',
    version=read_version("monci"),
    python_requires=">= 3.8",
    description="CI tool",
    long_description=open("README.md").read(),
    long_description_content_type="text/markdown",
    author='Enrico Zini',
    author_email='enrico@enricozini.org',
    url='https://github.com/ARPA-SIMC/moncic-ci/',
    license="GPLV2+",
    requires=["pyyaml"],
    # It does not make muc sense to run pip install without installing also
    # coloredlogs and texttable, although moncic-ci is able to work without
    # them
    install_requires=["pyyaml", "coloredlogs", "texttable"],
    packages=['moncic'],
    scripts=['monci'],
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: GNU General Public License v2 or later (GPLv2+)",
        "Topic :: Software Development :: Testing",
    ],
)
