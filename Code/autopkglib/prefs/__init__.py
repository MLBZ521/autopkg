#!/usr/local/autopkg/python

import json
import os
import plistlib
from copy import deepcopy
from typing import IO, Any, Dict, Optional, Union

import appdirs

from .. import get_pref, is_mac, log_err

try:
    from CoreFoundation import (
        CFPreferencesAppSynchronize,
        CFPreferencesCopyAppValue,
        CFPreferencesCopyKeyList,
        CFPreferencesSetAppValue,
        kCFPreferencesAnyHost,
        kCFPreferencesAnyUser,
        kCFPreferencesCurrentHost,
        kCFPreferencesCurrentUser,
    )
    from Foundation import NSArray, NSDictionary, NSNumber
except ImportError:
    if is_mac():
        print(
            "ERROR: Failed 'from Foundation import NSArray, NSDictionary' in "
            + __name__
        )
        print(
            "ERROR: Failed 'from CoreFoundation import "
            "CFPreferencesAppSynchronize, ...' in " + __name__
        )
        raise
    # On non-macOS platforms, the above imported names are stubbed out.
    NSArray = list
    NSDictionary = dict
    NSNumber = int

    def CFPreferencesAppSynchronize(*args, **kwargs):
        pass

    def CFPreferencesCopyAppValue(*args, **kwargs):
        pass

    def CFPreferencesCopyKeyList(*args, **kwargs):
        return []

    def CFPreferencesSetAppValue(*args, **kwargs):
        pass

    kCFPreferencesAnyHost = None
    kCFPreferencesAnyUser = None
    kCFPreferencesCurrentUser = None
    kCFPreferencesCurrentHost = None


APP_NAME = "Autopkg"
BUNDLE_ID = "com.github.autopkg"
# Type for methods that accept either a filesystem path or a file-like object.
FileOrPath = Union[IO, str, bytes, int]

# Type for ubiquitous dictionary type used throughout autopkg.
# Most commonly for `input_variables` and friends. It also applies to virtually all
# usages of plistlib results as well.
VarDict = Dict[str, Any]


class PreferenceError(Exception):
    """Preference exception"""

    pass


class Preferences:
    """An abstraction to hold all preferences."""

    def __init__(self):
        """Init."""
        self.prefs: VarDict = {}
        # What type of preferences input are we using?
        self.type: Optional[str] = None
        # Path to the preferences file we were given
        self.file_path: Optional[str] = None
        # If we're on macOS, read in the preference domain first.
        if is_mac():
            self.prefs = self._get_macos_prefs()
        else:
            self.prefs = self._get_file_prefs()
        if not self.prefs:
            log_err("WARNING: Did not load any default preferences.")

    def _parse_json_or_plist_file(self, file_path):
        """Parse the file. Start with plist, then JSON."""
        try:
            with open(file_path, "rb") as f:
                data = plistlib.load(f)
            self.type = "plist"
            self.file_path = file_path
            return data
        except Exception:
            pass
        try:
            with open(file_path, "rb") as f:
                data = json.load(f)
                self.type = "json"
                self.file_path = file_path
                return data
        except Exception:
            pass
        return {}

    def __deepconvert_objc(self, object):
        """Convert all contents of an ObjC object to Python primitives."""
        value = object
        if isinstance(object, NSNumber):
            value = int(object)
        elif isinstance(object, NSArray) or isinstance(object, list):
            value = [self.__deepconvert_objc(x) for x in object]
        elif isinstance(object, NSDictionary):
            value = dict(object)
            # RECIPE_REPOS is a dict of dicts
            for k, v in value.items():
                if isinstance(v, NSDictionary):
                    value[k] = dict(v)
        else:
            return object
        return value

    def _get_macos_pref(self, key):
        """Get a specific macOS preference key."""
        value = self.__deepconvert_objc(CFPreferencesCopyAppValue(key, BUNDLE_ID))
        return value

    def _get_macos_prefs(self):
        """Return a dict (or an empty dict) with the contents of all
        preferences in the domain."""
        prefs = {}

        # get keys stored via 'defaults write [domain]'
        user_keylist = CFPreferencesCopyKeyList(
            BUNDLE_ID, kCFPreferencesCurrentUser, kCFPreferencesAnyHost
        )

        # get keys stored via 'defaults write /Library/Preferences/[domain]'
        system_keylist = CFPreferencesCopyKeyList(
            BUNDLE_ID, kCFPreferencesAnyUser, kCFPreferencesCurrentHost
        )

        # CFPreferencesCopyAppValue() in get_macos_pref() will handle returning the
        # appropriate value using the search order, so merging prefs in order
        # here isn't necessary
        for keylist in [system_keylist, user_keylist]:
            if keylist:
                for key in keylist:
                    prefs[key] = self._get_macos_pref(key)
        return prefs

    def _get_file_prefs(self):
        r"""Lookup preferences for Windows in a standardized path, such as:
        * `C:\\Users\username\AppData\Local\Autopkg\config.{plist,json}`
        * `/home/username/.config/Autopkg/config.{plist,json}`
        Tries to find `config.plist`, then `config.json`."""

        config_dir = appdirs.user_config_dir(APP_NAME, appauthor=False)

        # Try a plist config, then a json config.
        data = self._parse_json_or_plist_file(os.path.join(config_dir, "config.plist"))
        if data:
            return data
        data = self._parse_json_or_plist_file(os.path.join(config_dir, "config.json"))
        if data:
            return data

        return {}

    def _set_macos_pref(self, key, value):
        """Sets a preference for domain"""
        try:
            CFPreferencesSetAppValue(key, value, BUNDLE_ID)
            if not CFPreferencesAppSynchronize(BUNDLE_ID):
                raise PreferenceError(f"Could not synchronize preference {key}")
        except Exception as err:
            raise PreferenceError(f"Could not set {key} preference: {err}") from err

    def read_file(self, file_path):
        """Read in a file and add the key/value pairs into preferences."""
        # Determine type or file: plist or json
        data = self._parse_json_or_plist_file(file_path)
        for k in data:
            self.prefs[k] = data[k]

    def _write_json_file(self):
        """Write out the prefs into JSON."""
        try:
            assert self.file_path is not None
            with open(self.file_path, "w") as f:
                json.dump(
                    self.prefs,
                    f,
                    skipkeys=True,
                    ensure_ascii=True,
                    indent=2,
                    sort_keys=True,
                )
        except Exception as e:
            log_err(f"Unable to write out JSON: {e}")

    def _write_plist_file(self):
        """Write out the prefs into a Plist."""
        try:
            assert self.file_path is not None
            with open(self.file_path, "wb") as f:
                plistlib.dump(self.prefs, f)
        except Exception as e:
            log_err(f"Unable to write out plist: {e}")

    def write_file(self):
        """Write preferences back out to file."""
        if not self.file_path:
            # Nothing to do if we weren't given a file
            return
        if self.type == "json":
            self._write_json_file()
        elif self.type == "plist":
            self._write_plist_file()

    def get_pref(self, key):
        """Retrieve a preference value."""
        return deepcopy(self.prefs.get(key))

    def get_all_prefs(self):
        """Retrieve a dict of all preferences."""
        return self.prefs

    def set_pref(self, key, value):
        """Set a preference value."""
        self.prefs[key] = value
        # On macOS, write it back to preferences domain if we didn't use a file
        if is_mac() and self.type is None:
            self._set_macos_pref(key, value)
        elif self.file_path is not None:
            self.write_file()
        else:
            log_err(f"WARNING: Preference change {key}=''{value}'' was not saved.")


def get_search_dirs():
    """Return search dirs from preferences or default list"""
    default = [".", "~/Library/AutoPkg/Recipes", "/Library/AutoPkg/Recipes"]

    dirs = get_pref("RECIPE_SEARCH_DIRS")
    if isinstance(dirs, str):
        # convert a string to a list
        dirs = [dirs]
    return dirs or default


def get_override_dirs():
    """Return override dirs from preferences or default list"""
    default = ["~/Library/AutoPkg/RecipeOverrides"]

    dirs = get_pref("RECIPE_OVERRIDE_DIRS")
    if isinstance(dirs, str):
        # convert a string to a list
        dirs = [dirs]
    return dirs or default
