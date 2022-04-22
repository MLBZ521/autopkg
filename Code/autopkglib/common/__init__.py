#!/usr/local/autopkg/python

import os
import plistlib
import sys
from distutils.version import LooseVersion
import re

import pkg_resources

RE_KEYREF = re.compile(r"%(?P<key>[a-zA-Z_][a-zA-Z_0-9]*)%")
# Supported recipe extensions
RECIPE_EXTS = (".recipe", ".recipe.plist", ".recipe.yaml")


def is_mac():
    """Return True if current OS is macOS."""
    return "darwin" in sys.platform.lower()


def is_windows():
    """Return True if current OS is Windows."""
    return "win32" in sys.platform.lower()


def is_linux():
    """Return True if current OS is Linux."""
    return "linux" in sys.platform.lower()


try:
    from Foundation import NSArray, NSDictionary, NSNumber
except ImportError:
    if is_mac():
        print(
            "ERROR: Failed 'from Foundation import NSArray, NSDictionary' in "
            + __name__
        )
        print(
            "ERROR: Failed 'from CoreFoundation import " "NSArray, ...' in " + __name__
        )
        raise
    # On non-macOS platforms, the above imported names are stubbed out.
    NSArray = list
    NSDictionary = dict
    NSNumber = int


def log(msg, error=False):
    """Message logger, prints to stdout/stderr."""
    if error:
        print(msg, file=sys.stderr)
    else:
        print(msg)


def log_err(msg):
    """Message logger for errors."""
    log(msg, error=True)


def get_autopkg_version():
    """Gets the version number of autopkg"""
    try:
        version_plist = plistlib.load(
            pkg_resources.resource_stream(__name__, "version.plist")
        )
    except Exception as ex:
        log_err(f"Unable to get autopkg version: {ex}")
        return "UNKNOWN"
    try:
        return version_plist["Version"]
    except (AttributeError, TypeError):
        return "UNKNOWN"


def version_equal_or_greater(this, that):
    """Compares two LooseVersion objects. Returns True if this is
    equal to or greater than that"""
    return LooseVersion(this) >= LooseVersion(that)


def update_data(a_dict, key, value):
    """Update a_dict keys with value. Existing data can be referenced
    by wrapping the key in %percent% signs."""

    def getdata(match):
        """Returns data from a match object"""
        return a_dict[match.group("key")]

    def do_variable_substitution(item):
        """Do variable substitution for item"""
        if isinstance(item, str):
            try:
                item = RE_KEYREF.sub(getdata, item)
            except KeyError as err:
                log_err(f"Use of undefined key in variable substitution: {err}")
        elif isinstance(item, (list, NSArray)):
            for index in range(len(item)):
                item[index] = do_variable_substitution(item[index])
        elif isinstance(item, (dict, NSDictionary)):
            # Modify a copy of the original
            if isinstance(item, dict):
                item_copy = item.copy()
            else:
                # Need to specify the copy is mutable for NSDictionary
                item_copy = item.mutableCopy()
            for key, value in list(item.items()):
                item_copy[key] = do_variable_substitution(value)
            return item_copy
        return item

    a_dict[key] = do_variable_substitution(value)


def is_executable(exe_path):
    """Is exe_path executable?"""
    return os.path.exists(exe_path) and os.access(exe_path, os.X_OK)


def _cmp(x, y):
    """
    Replacement for built-in function cmp that was removed in Python 3
    Compare the two objects x and y and return an integer according to
    the outcome. The return value is negative if x < y, zero if x == y
    and strictly positive if x > y.
    """
    return (x > y) - (x < y)


class APLooseVersion(LooseVersion):
    """Subclass of distutils.version.LooseVersion to fix issues under Python 3"""

    def _pad(self, version_list, max_length):
        """Pad a version list by adding extra 0 components to the end if needed."""
        # copy the version_list so we don't modify it
        cmp_list = list(version_list)
        while len(cmp_list) < max_length:
            cmp_list.append(0)
        return cmp_list

    def _compare(self, other):
        """Complete comparison mechanism since LooseVersion's is broken in Python 3."""
        if not isinstance(other, (LooseVersion, APLooseVersion)):
            other = APLooseVersion(other)
        max_length = max(len(self.version), len(other.version))
        self_cmp_version = self._pad(self.version, max_length)
        other_cmp_version = self._pad(other.version, max_length)
        cmp_result = 0
        for index, value in enumerate(self_cmp_version):
            try:
                cmp_result = _cmp(value, other_cmp_version[index])
            except TypeError:
                # integer is less than character/string
                if isinstance(value, int):
                    return -1
                return 1
            else:
                if cmp_result:
                    return cmp_result
        return cmp_result

    def __hash__(self):
        """Hash method."""
        return hash(self.version)

    def __eq__(self, other):
        """Equals comparison."""
        return self._compare(other) == 0

    def __ne__(self, other):
        """Not-equals comparison."""
        return self._compare(other) != 0

    def __lt__(self, other):
        """Less than comparison."""
        return self._compare(other) < 0

    def __le__(self, other):
        """Less than or equals comparison."""
        return self._compare(other) <= 0

    def __gt__(self, other):
        """Greater than comparison."""
        return self._compare(other) > 0

    def __ge__(self, other):
        """Greater than or equals comparison."""
        return self._compare(other) >= 0
