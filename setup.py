#!/usr/bin/env python3
# -*- coding: utf-8 -*-

'''

A package for encrypting files with a password.

Software:      netCDF Recompressor
Author:        Erick Edward Shepherd
E-mail:        dev@erickshepherd.com
GitHub:        https://www.github.com/ErickShepherd/netcdf_recompressor
PyPI:          https://pypi.org/project/netcdf_recompressor/
Date created:  2020-05-28
Last modified: 2020-05-28


Description:
    
    Installs netCDF Recompressor and its dependencies.


Copyright:
    
    netCDF Recompressor - A Python to to recompress netCDF and HDF files.
    
    Copyright (c) 2020 of Erick Edward Shepherd, all rights reserved.


License:
    
    This file is part of "netCDF Recompressor" (the "Software").
    
    MIT License

    Copyright (c) 2020 Erick Edward Shepherd

    Permission is hereby granted, free of charge, to any person obtaining a
    copy of this software and associated documentation files (the "Software"),
    to deal in the Software without restriction, including without limitation
    the right to use, copy, modify, merge, publish, distribute, sublicense,
    and/or sell copies of the Software, and to permit persons to whom the
    Software is furnished to do so, subject to the following conditions:

    The above copyright notice and this permission notice shall be included in
    all copies or substantial portions of the Software.

    THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
    IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
    FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
    AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
    LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING
    FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER
    DEALINGS IN THE SOFTWARE.

'''

# Third party imports.
import setuptools

# Local application imports.
import netcdf_recompressor
from netcdf_recompressor import constants

# Dunder definitions.
__author__  = netcdf_recompressor.__author__
__version__ = netcdf_recompressor.__version__

# Constant definitions.
GITHUB_URL  = "https://github.com/ErickShepherd/netcdf_recompressor"
PYPI_URL    = "https://pypi.org/project/netcdf_recompressor"
DESCRIPTION = __doc__.strip().split("\n")[0]

with open("README.rst", "r") as file:
    
    LONG_DESCRIPTION = file.read()

CLASSIFIERS = [
    "Intended Audience :: Developers",
    "Intended Audience :: End Users/Desktop",
    "System :: Archiving :: Compression",
    "Environment :: Console",
    "Development Status :: 5 - Production/Stable",
    "Natural Language :: English",
    "Programming Language :: Python",
    "Programming Language :: Python :: 3",
    "Programming Language :: Python :: 3.7",
    "License :: OSI Approved :: MIT License",
    "Operating System :: Microsoft :: Windows",
    "Operating System :: POSIX",
    "Operating System :: Unix",
    "Operating System :: MacOS"
]

PLATFORMS = ["Windows", "Linux", "Solaris", "Mac OS-X", "Unix"]

SETUP_KWARGS = dict(
    name                 = constants.PACKAGE_NAME,
    version              = __version__,
    description          = DESCRIPTION,
    long_description     = LONG_DESCRIPTION,
    classifiers          = CLASSIFIERS,
    author               = __author__,
    author_email         = "dev@erickshepherd.com",
    maintainer           = __author__,
    maintainer_email     = "dev@erickshepherd.com",
    license              = "MIT",
    platforms            = PLATFORMS,
    python_requires      = ">=3.7",
    packages             = setuptools.find_packages(),
    include_package_data = True,
    url                  = GITHUB_URL,
    download_url         = PYPI_URL,
    project_urls         = {
        "Bug Tracker" :
            GITHUB_URL + "/issues",
        
        "Source Code" :
            GITHUB_URL,
        
        "Documentation" :
            GITHUB_URL + "/blob/master/README.rst"
    }
)

if __name__ == "__main__":

    setuptools.setup(**SETUP_KWARGS)
