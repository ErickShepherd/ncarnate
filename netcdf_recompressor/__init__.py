#!/usr/bin/env python3
# -*- coding: utf-8 -*-

'''

A command line utility for the netCDF Recompressor package.

Software:      netCDF Recompressor
Author:        Erick Edward Shepherd
E-mail:        dev@erickshepherd.com
GitHub:        https://www.github.com/ErickShepherd/netcdf_recompressor
PyPI:          https://pypi.org/project/netcdf_recompressor/
Date created:  2020-05-27
Last modified: 2020-05-28


Description:
    
    A package for recompressing netCDF and HDF files.


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
import os

# Third party imports.
import netCDF4 as nc
import numpy as np

# Standard library imports.
from netcdf_recompressor import constants

# Dunder definitions.
__author__  = constants.__author__
__version__ = constants.__version__


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
