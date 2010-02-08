#  Copyright (c) 2009, Cloud Matrix Pty. Ltd.
#  All rights reserved; available under the terms of the BSD License.
"""

  esky.bdist_esky:  distutils command to freeze apps in esky format

Importing this module makes "bdist_esky" available as a distutils command.
This command will freeze the given scripts and package them into a zipfile
named with the application name, version and platform.

The resulting zipfile is conveniently in the format expected by the default
SimpleVersionFinder.  It will be named "appname-version.platform.zip"

"""


import os
import re
import sys
import shutil
import zipfile
from glob import glob

import distutils.command
from distutils.core import Command
from distutils.util import convert_path

from esky.util import get_platform, is_core_dependency
if sys.platform == "win32":
    from esky import winres
    from xml.dom import minidom


_FREEZERS = {}
try:
    from esky.bdist_esky import f_bbfreeze
    _FREEZERS["bbfreeze"] = f_bbfreeze
except ImportError:
    _FREEZERS["bbfreeze"] = None
try:
    from esky.bdist_esky import f_py2exe
    _FREEZERS["py2exe"] = f_py2exe
except ImportError:
    _FREEZERS["py2exe"] = None


class bdist_esky(Command):
    """Create a frozen application in 'esky' format.

    This distutils command can be used to freeze an application in the
    format expected by esky.  It interprets the following standard 
    distutils options:

       scripts:  list of scripts to freeze as executables;
                 to make a gui-only script, name it 'script.pyw'

       data_files:  copied into the frozen app directory

       package_data:  copied into library.zip alongside the module code

    To further customize the behaviour of the bdist_esky command, you can
    specify the following custom options:

        includes:  a list of modules to explicitly include in the freeze

        excludes:  a list of modules to explicitly exclude from the freeze

        freezer_module:  name of freezer module to use; currently only bbfreeze
                         and py2exe are supported.

        freezer_options: dict of options to pass through to the underlying
                         freezer module.

        bootstrap_module:  a custom module to use for esky bootstrapping;
                           the default calls esky.bootstrap.common.bootstrap()

        bundle_msvcrt:  whether to bundle the MSVCRT DLLs, manifest files etc
                        as a private assembly.  The default is False; only
                        those with a valid license to redistriute these files
                        should enable it.
    
    """

    description = "create a frozen app in 'esky' format"

    user_options = [
                    ('dist-dir=', 'd',
                     "directory to put final built distributions in"),
                    ('freezer-module=', None,
                     "module to use for freezing the application"),
                    ('freezer-options=', None,
                     "options to pass to the underlying freezer module"),
                    ('bootstrap-module=', None,
                     "module to use for bootstrapping the application"),
                    ('bundle-msvcrt=', None,
                     "whether to bundle MSVCR as private assembly"),
                    ('includes=', None,
                     "list of modules to specifically include"),
                    ('excludes=', None,
                     "list of modules to specifically exclude"),
                   ]

    boolean_options = ["bundle-msvcrt"]

    def initialize_options(self):
        self.dist_dir = None
        self.includes = []
        self.excludes = []
        self.freezer_module = None
        self.freezer_options = {}
        self.bundle_msvcrt = False
        self.bootstrap_module = None

    def finalize_options(self):
        self.set_undefined_options('bdist',('dist_dir', 'dist_dir'))
        if self.freezer_module is None:
            try:
                freezer = _FREEZERS.itervalues().next()
            except StopIteration:
                err = "no supported freezer modules found"
                err += " (try installing bbfreeze)"
                raise RuntimeError(err)
        else:
            try:
                freezer = _FREEZERS[self.freezer_module]
            except KeyError:
                err = "freezer module not supported: '%s'"
                err = err % (self.freezer_module,)
                raise RuntimeError(err)
            else:
                if freezer is None:
                    err = "freezer module not found: '%s'"
                    err = err % (self.freezer_module,)
                    raise RuntimeError(err)
            self.freezer_module = freezer

    def run(self):
        #  Create the dirs into which to freeze the app
        fullname = self.distribution.get_fullname()
        platform = get_platform()
        self.bootstrap_dir = os.path.join(self.dist_dir,
                                          "%s.%s"%(fullname,platform,))
        self.freeze_dir = os.path.join(self.bootstrap_dir,
                                       "%s.%s"%(fullname,platform,))
        if os.path.exists(self.bootstrap_dir):
            shutil.rmtree(self.bootstrap_dir)
        os.makedirs(self.freeze_dir)
        #  Hand things off to the selected freezer module
        self.freezer_module.freeze(self)
        #  Zip up the distribution
        zfname = os.path.join(self.dist_dir,"%s.%s.zip"%(fullname,platform,))
        zf = zipfile.ZipFile(zfname,"w")
        for (dirpath,dirnames,filenames) in os.walk(self.bootstrap_dir):
            for fn in filenames:
                fpath = os.path.join(dirpath,fn)
                zpath = fpath[len(self.bootstrap_dir)+1:]
                zf.write(fpath,zpath)
        zf.close()
        shutil.rmtree(self.bootstrap_dir)

    def get_scripts(self):
        """Yield paths of all scripts to be included in the freeze."""
        if self.distribution.has_scripts():
            for s in self.distribution.scripts:
                yield s

    def get_data_files(self):
        """Yield (source,destination) tuples for data files.

        This method generates the names of all data file to be included in
        the frozen app.  They should be placed directly into the freeze
        directory as raw files.
        """
        fdir = self.freeze_dir
        if sys.platform == "win32" and self.bundle_msvcrt:
            for (src,dst) in self.get_msvcrt_private_assembly_files():
                yield (src,dst)
        if self.distribution.data_files:
            for datafile in self.distribution.data_files:
                #  Plain strings get placed in the root dist directory.
                if isinstance(datafile,basestring):
                    datafile = ("",[datafile])
                (dst,sources) = datafile
                if os.path.isabs(dst):
                    err = "cant freeze absolute data_file paths (%s)"
                    err = err % (dst,)
                    raise ValueError(err)
                dst = convert_path(dst)
                for src in sources:
                    src = convert_path(src)
                    yield (src,os.path.join(dst,os.path.basename(src)))
 
    def get_package_data(self):
        """Yield (source,destination) tuples for package data files.

        This method generates the names of all package data files to be
        included in the frozen app.  They should be placed in the library.zip
        or equivalent, alongside the python files for that package.
        """
        if self.distribution.package_data:
            for pkg,data in self.distribution.package_data.iteritems():
                pkg_dir = self.get_package_dir(pkg)
                pkg_path = pkg.replace(".","/")
                if isinstance(data,basestring):
                    data = [data]
                for dpattern in data:
                    dfiles = glob(os.path.join(pkg_dir,convert_path(dpattern)))
                    for nm in dfiles:
                        arcnm = pkg_path + nm[len(pkg_dir):]
                        yield (nm,arcnm)

    def get_package_dir(self,pkg):
        """Return directory where the given package is located.

        This was largely swiped from distutils, with some cleanups.
        """
        inpath = pkg.split(".")
        outpath = []
        if not self.distribution.package_dir:
            outpath = inpath
        else:
            while inpath:
                try:
                    dir = self.distribution.package_dir[".".join(inpath)]
                except KeyError:
                    outpath.insert(0, inpath[-1])
                    del inpath[-1]
                else:
                    outpath.insert(0, dir)
                    break
            else:
                try:
                    dir = self.package_dir[""]
                except KeyError:
                    pass
                else:
                    outpath.insert(0, dir)
        if outpath:
            return os.path.join(*outpath)
        else:
            return ""

    def get_msvcrt_private_assembly_files(self):
        """Get (source,destination) tuples for the MSVCRT DLLs, manifest etc.

        This method generates data_files tuples for the MSVCRT DLLs, manifest
        and associated paraphernalia.  Including these files is required for
        newer Python versions if you want to run on machines that don't have
        the latest C runtime installed *and* you don't want to run the special
        "vcredist_x86.exe" program during your installation process.

        Bundling is only perform on win32 paltforms, and only if you explicitly         enable it.  Before doing so, carefully check whether you have a license
        to distribute these files.
        """
        msvcrt_info = self._get_msvcrt_info()
        if msvcrt_info is not None:
            msvcrt_name = msvcrt_info[0]
            #  Find installed manifest file with matching info
            for manifest_file in self._find_msvcrt_manifest_files(msvcrt_name):
                try:
                    with open(manifest_file,"rb") as mf:
                        manifest_data = mf.read()
                        for info in msvcrt_info:
                            if info.encode() not in manifest_data:
                                break
                        else:
                            break
                except EnvironmentError:
                    pass
            else:
                err = "manifest for %s not found" % (msvcrt_info,)
                raise RuntimeError(err)
            #  Copy the manifest and matching directory into the freeze dir.
            #  The manifest file might be next to the dir, inside the dir, or
            #  in a subdir named "Manifests".  Walk around till we find it.
            msvcrt_dir = ".".join(manifest_file.split(".")[:-1])
            if not os.path.isdir(msvcrt_dir):
                msvcrt_basename = os.path.basename(msvcrt_dir)
                msvcrt_parent = os.path.dirname(os.path.dirname(msvcrt_dir))
                msvcrt_dir = os.path.join(msvcrt_parent,msvcrt_basename)
                if not os.path.isdir(msvcrt_dir):
                    msvcrt_dir = os.path.join(msvcrt_parent,msvcrt_name)
                    if not os.path.isdir(msvcrt_dir):
                        err = "manifest for %s not found" % (msvcrt_info,)
                        raise RuntimeError(err)
            manifest_name = msvcrt_name + ".manifest"
            yield (manifest_file,os.path.join(msvcrt_name,manifest_name))
            for fnm in os.listdir(msvcrt_dir):
                yield (os.path.join(msvcrt_dir,fnm),
                       os.path.join(msvcrt_name,fnm))

    def _get_msvcrt_info(self):
        """Get info about the MSVCRT in use by this python executable.

        This parses the name, version and public key token out of the exe
        manifest and returns them as a tuple.
        """
        try:
            manifest_str = winres.get_app_manifest()
        except EnvironmentError:
            return None
        manifest = minidom.parseString(manifest_str)
        assembly = manifest.getElementsByTagName("assemblyIdentity")[0]
        name = assembly.attributes["name"].value
        version = assembly.attributes["version"].value 
        pubkey = assembly.attributes["publicKeyToken"].value 
        return (name,version,pubkey)
        
    def _find_msvcrt_manifest_files(self,name):
        """Search the system for candidate MSVCRT manifest files."""
        #  Search for redist files in a Visual Studio install
        progfiles = os.path.expandvars("%PROGRAMFILES%")
        for dnm in os.listdir(progfiles):
            if dnm.startswith("Microsoft Visual Studio"):
                dpath = os.path.join(progfiles,dnm,"VC","redist")
                for (subdir,_,filenames) in os.walk(dpath):
                    for fnm in filenames:
                        if name in fnm and fnm.endswith(".manifest"):
                            yield os.path.join(subdir,fnm)
        #  Search for manifests installed in the WinSxS directory
        winsxs_m = os.path.expandvars("%WINDIR%\\WinSxS\\Manifests")
        for fnm in os.listdir(winsxs_m):
            if name in fnm and fnm.endswith(".manifest"):
                yield os.path.join(winsxs_m,fnm)
        winsxs = os.path.expandvars("%WINDIR%\\WinSxS")
        for fnm in os.listdir(winsxs):
            if name in fnm and fnm.endswith(".manifest"):
                yield os.path.join(winsxs,fnm)

    def copy_to_bootstrap_env(self,src,dst=None):
        """Copy the named file into the bootstrap environment.

        The filename is also added to the bootstrap manifest.
        """
        if dst is None:
            dst = os.path.basename(src)
        srcpath = os.path.join(self.freeze_dir,src)
        dstpath = os.path.join(self.bootstrap_dir,dst)
        if os.path.isdir(srcpath):
            self.copy_tree(srcpath,dstpath)
        else:
            self.copy_file(srcpath,dstpath)
        f_manifest = os.path.join(self.freeze_dir,"esky-bootstrap.txt")
        f_manifest = open(f_manifest,"at")
        f_manifest.seek(0,os.SEEK_END)
        f_manifest.write(dst)
        f_manifest.write("\n")
        f_manifest.close()
        return dstpath


distutils.command.__all__.append("bdist_esky")
sys.modules["distutils.command.bdist_esky"] = sys.modules["esky.bdist_esky"]
