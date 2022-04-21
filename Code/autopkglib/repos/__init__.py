#!/usr/local/autopkg/python
import os
from urllib.parse import urlparse

from autopkglib import get_pref, log, log_err
from autopkglib.git import GitError, git_cmd, run_git
from autopkglib.prefs import get_search_dirs


def expand_repo_url(url):
    """Given a GitHub repo URL-ish name, returns a full GitHub URL. Falls
    back to the 'autopkg' GitHub org, and full non-GitHub URLs return
    unmodified.
    Examples:
    'user/reciperepo'         -> 'https://github.com/user/reciperepo'
    'reciperepo'              -> 'https://github.com/autopkg/reciperepo'
    'http://some/repo/url     -> 'http://some/repo/url'
    'git@server:repo/url      -> 'ssh://git@server/repo/url'
    '/some/path               -> '/some/path'
    '~/some/path              -> '~/some/path'
    """
    # Strip trailing slashes
    url = url.rstrip("/")
    # Parse URL to determine scheme
    parsed_url = urlparse(url)
    if url.startswith(("/", "~")):
        # If the URL looks like a file path, return as is.
        pass
    elif not parsed_url.scheme:
        if ":" in parsed_url.path and (
            "/" not in parsed_url.path
            or parsed_url.path.find(":") < parsed_url.path.find("/")
        ):
            # If no URL scheme was given check for scp-like syntax, where there is
            # no slash before the first colon and convert this to a valid ssh url
            url = url.replace(":", "/", 1)
            url = f"ssh://{url}"
        # If no URL scheme was given in the URL, try GitHub URLs
        elif "/" in url:
            # If URL looks like 'name/repo' then prepend the base GitHub URL
            url = f"https://github.com/{url}"
        else:
            # Assume it's a repo within the 'autopkg' org
            url = f"https://github.com/autopkg/{url}"

    return url


def pull_repo(repo_url):
    if "file://" in repo_url:
        log_err(
            "AutoPkg does not handle file:// URIs; "
            "add to your local Recipes folder instead."
        )
        return
    repo_url = expand_repo_url(repo_url)
    new_recipe_repo_dir = get_recipe_repo(repo_url)
    return new_recipe_repo_dir


def get_recipe_repo(git_path):
    """git clone git_path to local disk and return local path"""

    # figure out a local directory name to clone to
    parts = urlparse(git_path)
    domain_and_port = parts.netloc
    # discard user name if any
    if "@" in domain_and_port:
        domain_and_port = domain_and_port.split("@", 1)[1]
    # discard port if any
    domain = domain_and_port.split(":")[0]
    reverse_domain = ".".join(reversed(domain.split(".")))
    # discard file extension if any
    url_path = os.path.splitext(parts.path)[0]
    dest_name = reverse_domain + url_path.replace("/", ".")
    recipe_repo_dir = get_pref("RECIPE_REPO_DIR") or "~/Library/AutoPkg/RecipeRepos"
    recipe_repo_dir = os.path.expanduser(recipe_repo_dir)
    dest_dir = os.path.join(recipe_repo_dir, dest_name)
    dest_dir = os.path.abspath(dest_dir)
    gitcmd = git_cmd()
    if not gitcmd:
        log_err("No git binary could be found!")
        return None

    if os.path.exists(dest_dir):
        # probably should attempt a git pull
        # check to see if this is really a git repo first
        if not os.path.isdir(os.path.join(dest_dir, ".git")):
            log_err(f"{dest_dir} exists and is not a git repo!")
            return None
        log(f"Attempting git pull for {dest_dir}...")
        try:
            log(run_git(["pull"], git_directory=dest_dir))
            return dest_dir
        except GitError as err:
            log_err(err)
            return None
    else:
        log(f"Attempting git clone for {git_path}...")
        try:
            log(run_git(["clone", git_path, dest_dir]))
            return dest_dir
        except GitError as err:
            log_err(err)
            return None
    return None


def add_repo(arguments):
    recipe_search_dirs = get_search_dirs()
    recipe_repos = get_pref("RECIPE_REPOS") or {}
    for repo_url in arguments:
        new_recipe_repo_dir = pull_repo(repo_url)
        if new_recipe_repo_dir:
            if new_recipe_repo_dir not in recipe_search_dirs:
                log(f"Adding {new_recipe_repo_dir} to RECIPE_SEARCH_DIRS...")
                recipe_search_dirs.append(new_recipe_repo_dir)
            # add info about this repo to our prefs
            recipe_repos[new_recipe_repo_dir] = {"URL": repo_url}
    return (recipe_search_dirs, recipe_repos)
