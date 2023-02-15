#!/usr/bin/env python3
# coding: utf-8

from setuptools import setup


setup(
    name='moncic-ci',
    python_requires=">= 3.8",
    description="CI tool",
    long_description=open("README.md").read(),
    long_description_content_type="text/markdown",
    author='Enrico Zini',
    author_email='enrico@enricozini.org',
    url='https://github.com/ARPA-SIMC/moncic-ci/',
    license="GPLV2+",
    # It does not make muc sense to run pip install without installing also
    # coloredlogs and texttable, although moncic-ci is able to work without
    # them
    install_requires=["pyyaml", "ruamel.yaml", "coloredlogs", "texttable", "requests", "tblib", "GitPython"],
    packages=['moncic', "moncic.build", "moncic.cli", "moncic.distro", "moncic.source", "moncic.utils"],
    scripts=['monci'],
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: GNU General Public License v2 or later (GPLv2+)",
        "Topic :: Software Development :: Testing",
    ],
)
