#!/usr/bin/env python3
# -*- coding: utf-8 -*-

'''

A package for encrypting files with a password.

Software:      netCDF Recompressor
Author:        Erick Edward Shepherd
E-mail:        dev@erickshepherd.com
GitHub:        https://www.github.com/ErickShepherd/netcdf_recompressor
PyPI:          https://pypi.org/project/netcdf_recompressor/
Date created:  2020-05-27
Last modified: 2020-05-28


Description:
    
    A command line utility for the netCDF Recompressor package, which allows
    users to alter the compression of a supported netCDF or HDF file.

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

# Standard library imports.
import argparse
import datetime
import logging
import os
import sys
import traceback

# Third party imports.
import netCDF4 as nc
import numpy as np
from tqdm import tqdm

# Constant definitions.
SUPPORTED_EXTENSIONS = ["nc", "hdf"]


def recompress(src       : str,
               dst       : str  = None,
               zlib      : bool = True,
               shuffle   : bool = True,
               complevel : int  = 7,
               overwrite : bool = True) -> None:
    
    if dst is None:
        
        filename, file_extension = os.path.splitext(src)
        dst = filename + "_recompressed" + file_extension
    
    src_path = os.path.abspath(src)
    dst_path = os.path.abspath(dst)
    src_file = nc.Dataset(src_path, mode = "r")
    dst_file = nc.Dataset(dst_path, mode = "w")
    
    _copy_dimensions(src_file, dst_file)
    _copy_attributes(src_file, dst_file)
    _copy_variables(src_file, dst_file, zlib, shuffle, complevel)
    _copy_groups(src_file, dst_file, zlib, shuffle, complevel)
    
    # Closes both files.
    src_file.close()
    dst_file.close()
    
    # Replaces the original file with the re-compressed file.
    if overwrite:
        
        os.replace(dst_path, src_path)


def _copy_dimensions(src_obj : str, dst_obj : str) -> None:
    
    # Copies the dimensions of the source file or group.
    for name, dimension in src_obj.dimensions.items():
                
        if dimension.isunlimited():
            
            size = None
            
        else:
            
            size = dimension.size
        
        dst_obj.createDimension(name, size)


def _copy_attributes(src_obj : str, dst_obj : str) -> None:
    
    # Copies the global attributes of the source file, group, or variable.
    attributes = {attr : src_obj.getncattr(attr) for attr in src_obj.ncattrs()}
    dst_obj.setncatts(attributes)
    

def _copy_variables(src_obj   : str,
                    dst_obj   : str,
                    zlib      : bool,
                    shuffle   : bool,
                    complevel : int) -> None:
    
    # Copies the variables of the source file or group.
    for name, src_var in src_obj.variables.items():
        
        dtype      = src_var.dtype
        dimensions = src_var.dimensions
        
        if isinstance(dtype, np.dtype):
            
            if dtype.isnative:
                
                endian = "native"
                        
            elif dtype.str.startswith(">"):

                endian = "big"

            elif dtype.str.startswith("<"):

                endian = "little"
            
        else:
            
            endian = "native"
        
        variable_kwargs = {
            "endian"    : endian,
            "zlib"      : zlib,
            "shuffle"   : shuffle,
            "complevel" : complevel
        }
        
        dst_obj.createVariable(name, dtype, dimensions, **variable_kwargs)
        dst_var = dst_obj.variables[name]
        
        # Copies the variable attributes.
        _copy_attributes(src_var, dst_var)

        # Copies the variables values.
        dst_var[:] = src_var[:]


def _copy_groups(src_obj   : str,
                 dst_obj   : str,
                 zlib      : bool,
                 shuffle   : bool,
                 complevel : int) -> None:
    
    for name, src_group in src_obj.groups.items():
        
        dst_group = dst_obj.createGroup(name)
        
        _copy_dimensions(src_group, dst_group)
        _copy_attributes(src_group, dst_group)
        _copy_variables(src_group, dst_group, zlib, shuffle, complevel)
        _copy_groups(src_group, dst_group, zlib, shuffle, complevel)
        
        
def _validate_extension(path : str) -> bool:
    
    filename, file_extension = os.path.splitext(path)
    
    for extension in SUPPORTED_EXTENSIONS:
        
        if extension.lower() in file_extension:
            
            return True
        
    else:
        
        return False
        
        
def _files_to_paths(root : str, files : list) -> list:
    
    paths = [os.path.join(root, file) for file in files]
    
    return paths
    
        
def _get_files(paths : str, recursive : bool) -> int:

    paths = [os.path.abspath(path) for path in paths]
    files = []
        
    for path in paths:
        
        if os.path.isdir(path):

            if recursive:

                for root, subdirectories, subfiles in os.walk(path):
                    
                    subfiles    = _files_to_paths(root, subfiles)
                    valid_files = list(filter(_validate_extension, subfiles))

                    files += valid_files

            else:
                
                subfiles    = _files_to_paths(path, os.listdir(path))
                subfiles    = list(filter(os.path.isfile, subfiles))
                valid_files = list(filter(_validate_extension, subfiles))
                
                files += valid_files

        elif os.path.isfile(path):
            
            if _validate_extension(path):
            
                files += [path]

        else:

            raise ValueError
        
    return files
     
    
def _build_argument_parser():
    
    parser = argparse.ArgumentParser(
        description = "Alters the compression of HDF5/netCDF4 files."
    )
    
    parser.add_argument(
        "path",
        type  = str,
        nargs = "+",
        help  = "The path(s) to the file(s) to alter."
    )
    
    parser.add_argument(
        "--complevel",
        type    = int,
        default = 7,
        choices = list(range(10)),
        help    = "The desired gzip deflate compression level."
    )
    
    group = parser.add_mutually_exclusive_group(required = False)
    
    group.add_argument(
        "--zlib",
        dest   = "zlib",
        action = "store_true",
        help   = "Enables zlib gzip compression."
    )
    
    group.add_argument(
        "--no-zlib",
        dest   = "zlib",
        action = "store_false",
        help   = "Disables zlib gzip compression."
    )
    
    parser.set_defaults(**{"zlib" : True})
    
    group = parser.add_mutually_exclusive_group(required = False)
    
    group.add_argument(
        "--shuffle",
        dest   = "shuffle",
        action = "store_true",
        help   = "Enables the HDF5 shuffle filter."
    )
    
    group.add_argument(
        "--no-shuffle",
        dest   = "shuffle",
        action = "store_false",
        help   = "Disables the HDF5 shuffle filter."
    )
    
    parser.set_defaults(**{"shuffle" : True})
    
    group = parser.add_mutually_exclusive_group(required = False)
    
    group.add_argument(
        "--overwrite",
        dest   = "overwrite",
        action = "store_true",
        help   = "Only the re/de-compressed file(s) is/are kept."
    )
    
    group.add_argument(
        "--no-overwrite",
        dest   = "overwrite",
        action = "store_false",
        help   = "Both the re/de-compressed and original file(s) is/are kept."
    )
    
    parser.set_defaults(**{"overwrite" : True})
    
    parser.add_argument(
        "-r",
        "--recursive",
        dest   = "recursive",
        action = "store_true",
        help   = "Acts recursively on the given director(y/ies)."
    )
    
    parser.set_defaults(**{"recursive" : False})
    
    return parser


def _build_logger():
    
    logger    = logging.getLogger("myapp")
    handler   = logging.FileHandler("netcdf4_compressor_log.txt")
    formatter = logging.Formatter("-" * 79 + "\n" + " ".join(sys.argv) + "\n%(asctime)s %(levelname)s %(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.WARNING)
    
    return logger
    
    
if __name__ == "__main__":
    
    logger    = _build_logger()
    parser    = _build_argument_parser()
    kwargs    = vars(parser.parse_args())
    paths     = kwargs.pop("path")
    recursive = kwargs.pop("recursive")
    files     = _get_files(paths, recursive)
    
    for file in tqdm(files, desc = "Files re/de-compressed"):
        
        try:
            
            #raise ValueError
            recompress(file, **kwargs)
        
        except Exception:
            
            logger.exception("ererere")
