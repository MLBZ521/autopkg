#!/usr/local/munki/autopkg

import subprocess

from .. import find_binary


def git_cmd():
    """Returns a path to a git binary, priority in the order below.
    Returns None if none found.
    1. app pref 'GIT_PATH'
    2. a 'git' binary that can be found in the PATH environment variable
    3. '/usr/bin/git'
    """
    return find_binary("git")


class GitError(Exception):
    """Exception to throw if git fails"""

    pass


def run_git(git_options_and_arguments, git_directory=None):
    """Run a git command and return its output if successful;
    raise GitError if unsuccessful."""
    gitcmd = git_cmd()
    if not gitcmd:
        raise GitError("ERROR: git is not installed!")
    cmd = [gitcmd]
    cmd.extend(git_options_and_arguments)
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=git_directory,
            text=True,
        )
        (cmd_out, cmd_err) = proc.communicate()
    except OSError as err:
        raise GitError from OSError(
            f"ERROR: git execution failed with error code {err.errno}: "
            f"{err.strerror}"
        )
    if proc.returncode != 0:
        raise GitError(f"ERROR: {cmd_err}")
    else:
        return cmd_out
