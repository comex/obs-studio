#!/usr/bin/env python
from os import makedirs, rename, walk, path as ospath
from os.path import basename
from sys import argv

candidate_paths = [
	("bin", "Contents/MacOS"),
	("obs-plugins", "Contents/Plugins"),
	("data", "Contents/Resources/data"),
]

obs_src = ospath.join(ospath.dirname(argv[0]), '../../..')
plist_path = ospath.join(obs_src, 'cmake/osxbundle/Info.plist')
icon_path = ospath.join(obs_src, 'cmake/osxbundle/obs.icns')

#not copied
blacklist = """/usr /System""".split()

#copied
whitelist = """/usr/local""".split()

#
#
#


from glob import glob
from subprocess import check_output, call
from collections import namedtuple
from shutil import copy, copyfile, copytree, rmtree, ignore_patterns
import plistlib
import re
import argparse

def _str_to_bool(s):
    """Convert string to bool (in argparse context)."""
    if s.lower() not in ['true', 'false']:
        raise ValueError('Need bool; got %r' % s)
    return {'true': True, 'false': False}[s.lower()]

def add_boolean_argument(parser, name, default=False):
    """Add a boolean argument to an ArgumentParser instance."""
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        '--' + name, nargs='?', default=default, const=True, type=_str_to_bool)
    group.add_argument('--no' + name, dest=name, action='store_false')

parser = argparse.ArgumentParser(description='obs-studio package util')
parser.add_argument('-d', '--base-dir', dest='dir', default='rundir/RelWithDebInfo')
parser.add_argument('-n', '--build-number', dest='build_number', default='0')
parser.add_argument('-k', '--public-key', dest='public_key', default='OBSPublicDSAKey.pem')
parser.add_argument('-b', '--base-url', dest='base_url', default='https://obsproject.com/osx_update')
parser.add_argument('-u', '--user', dest='user', default='jp9000')
parser.add_argument('-c', '--channel', dest='channel', default='master')
add_boolean_argument(parser, 'stable', default=False)
parser.add_argument('-p', '--prefix', dest='prefix', default='')
args = parser.parse_args()

def cmd(cmd, **kwargs):
    import subprocess
    import shlex
    return subprocess.check_output(shlex.split(cmd), **kwargs).rstrip('\r\n')

LibTarget = namedtuple("LibTarget", ("bin_source", "external", "bin_target", "copy_source", "copy_target"))

inspect = list()

inspected = set()

build_path = args.dir
build_path = build_path.replace("\\ ", " ")

dylib_path_prefix = "@executable_path/../.."

def add(bin_source, external=False, bin_target=None):
	copy_source = None
	copy_target = None
	if bin_target is None:
		assert external
		split = bin_source.split("/")
		for i, bit in enumerate(split):
			if bit.endswith(".framework"):
				# /path/to/Foo.framework
				copy_source = "/".join(split[:i+1])
				# Contents/Frameworks/Foo.framework
				copy_target = ospath.join("Contents/Frameworks", split[i])
				# Contents/Frameworks/Foo.framework/Versions/A/Foo
				bin_target = ospath.join(copy_target, "/".join(split[i+1:]))
				break
		else:
			bin_target = ospath.join("Contents/MacOS", split[-1])
	if not external:
		bin_source = ospath.join(build_path, bin_source)
	copy_source = copy_source or bin_source
	copy_target = copy_target or bin_target
	assert not ospath.isabs(copy_target)
	t = LibTarget(bin_source, external, bin_target, copy_source, copy_target)
	if t in inspected:
		return
	inspect.append(t)
	inspected.add(t)


info = plistlib.readPlist(plist_path)

for i, copy_base in candidate_paths:
	copytree(ospath.join(build_path, i), ospath.join("tmp", copy_base), symlinks=True)
	print("Checking " + i)
	for root, dirs, files in walk(build_path+"/"+i):
		for file_ in files:
			if ".ini" in file_:
				continue
			if ".png" in file_:
				continue
			if ".effect" in file_:
				continue
			if ".py" in file_:
				continue
			if ".json" in file_:
				continue
			path = root + "/" + file_
			try:
				out = check_output("{0}otool -L '{1}'".format(args.prefix, path), shell=True,
						universal_newlines=True)
				if "is not an object file" in out:
					continue
			except:
				continue
			rel_path = ospath.relpath(path, build_path)
			print(repr(path), repr(rel_path))
			add(rel_path, False, ospath.join(copy_base, i))

def add_plugins(path, replace):
	for img in glob(path.replace(
		"lib/QtCore.framework/Versions/5/QtCore",
		"plugins/%s/*"%replace).replace(
			"Library/Frameworks/QtCore.framework/Versions/5/QtCore",
			"share/qt5/plugins/%s/*"%replace)):
		if "_debug" in img:
			continue
		add(img, True, ospath.join("Contents/MacOS", img.split("plugins/")[-1]))

seen_paths_for_dylib = {}

while inspect:
	target = inspect.pop()
	print("inspecting", repr(target))
	path = target.bin_source
	if path[0] == "@":
		continue
	out = check_output("{0}otool -L '{1}'".format(args.prefix, path), shell=True,
			universal_newlines=True)

	if "QtCore" in path:
		add_plugins(path, "platforms")
		add_plugins(path, "imageformats")
		add_plugins(path, "accessible")
		add_plugins(path, "styles")


	otool_paths = [line.strip().split(" (")[0] for line in out.split("\n")[1:]]

	for line in out.split("\n")[1:]:
		new = line.strip().split(" (")[0]
		bn = basename(new)

		seen_paths_for_dylib.setdefault(bn, set()).add(new)

		if bn == basename(path):
			# This is the ID_DYLIB, not a dependency
			continue

		if not new or new[0] == "@" or new.endswith(path.split("/")[-1]):
			continue
		whitelisted = False
		for i in whitelist:
			if new.startswith(i):
				whitelisted = True
		if not whitelisted:
			blacklisted = False
			for i in blacklist:
				if new.startswith(i):
					blacklisted = True
					break
			if blacklisted:
				continue
		add(new, True)

changes = list()
for bin_source, external, bin_target, copy_source, copy_target in inspected:
	for seen_path in seen_paths_for_dylib.get(basename(bin_source), set()):
		changes.append("-change '%s' '%s/%s'"%(seen_path, dylib_path_prefix, bin_target))
changes = " ".join(changes)

latest_tag = cmd('git describe --tags --abbrev=0', cwd=obs_src)
log = cmd('git log --pretty=oneline {0}...HEAD'.format(latest_tag), cwd=obs_src)

from os import path
# set version
if args.stable:
    info["CFBundleVersion"] = latest_tag
    info["CFBundleShortVersionString"] = latest_tag
    info["SUFeedURL"] = '{0}/stable/updates.xml'.format(args.base_url)
else:
    info["CFBundleVersion"] = args.build_number
    info["CFBundleShortVersionString"] = '{0}.{1}'.format(latest_tag, args.build_number)
    info["SUFeedURL"] = '{0}/{1}/{2}/updates.xml'.format(args.base_url, args.user, args.channel)

info["SUPublicDSAKeyFile"] = path.basename(args.public_key)
info["OBSFeedsURL"] = '{0}/feeds.xml'.format(args.base_url)

app_name = info["CFBundleName"]+".app"
icon_file = "tmp/Contents/Resources/%s"%info["CFBundleIconFile"]

copy(icon_path, icon_file)
plistlib.writePlist(info, "tmp/Contents/Info.plist")
try:
	copy(args.public_key, "tmp/Contents/Resources")
except:
	pass

for bin_source, external, bin_target, copy_source, copy_target in inspected:
	id_ = "-id '{0}/{1}'".format(dylib_path_prefix, bin_target)
	copy_target = ospath.join("tmp", copy_target)

	if external:
		assert basename(copy_target) != "Python"
		dn = ospath.dirname(copy_target)
		if not ospath.exists(dn):
			makedirs(dn)
		if ospath.exists(copy_target):
			pass
		elif ospath.isfile(copy_source):
			copyfile(copy_source, copy_target)
		else:
			copytree(copy_source, copy_target, symlinks=True,
				ignore=ignore_patterns("Headers"))

	icmd = "{0}install_name_tool {1} {2} '{3}'".format(args.prefix, changes, id_, bin_target)
	call(icmd, shell=True)

try:
	rename("tmp", app_name)
except:
	print("App already exists")
	rmtree("tmp")
