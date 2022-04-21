#!/usr/local/autopkg/python

import difflib
import glob
import os
import plistlib
import pprint

import yaml
from autopkglib.github import (
    GitHubSession,
    do_gh_repo_contents_fetch,
    print_gh_search_results,
)
from autopkglib.prefs import get_override_dirs, get_search_dirs

from .. import get_pref, log, log_err, version_equal_or_greater

# Supported recipe extensions
RECIPE_EXTS = (".recipe", ".recipe.plist", ".recipe.yaml")


class RecipeLoadingException(Exception):
    """Represent an exception assembling a recipe"""

    pass


def recipe_has_step_processor(recipe, processor):
    """Does the recipe object contain at least one step with the
    named Processor?"""
    if "Process" in recipe:
        processors = [step.get("Processor") for step in recipe["Process"]]
        if processor in processors:
            return True
    return False


def has_munkiimporter_step(recipe):
    """Does the recipe have a MunkiImporter step?"""
    return recipe_has_step_processor(recipe, "MunkiImporter")


def has_check_phase(recipe):
    """Does the recipe have a "check" phase?"""
    return recipe_has_step_processor(recipe, "EndOfCheckPhase")


def builds_a_package(recipe):
    """Does this recipe build any packages?"""
    return recipe_has_step_processor(recipe, "PkgCreator")


def remove_recipe_extension(name):
    """Removes supported recipe extensions from a filename or path.
    If the filename or path does not end with any known recipe extension,
    the name is returned as is."""
    for ext in RECIPE_EXTS:
        if name.endswith(ext):
            return name[: -len(ext)]
    return name


def get_identifier(recipe):
    """Return identifier from recipe dict. Tries the Identifier
    top-level key and falls back to the legacy key location."""
    try:
        return recipe["Identifier"]
    except (KeyError, AttributeError):
        try:
            return recipe["Input"]["IDENTIFIER"]
        except (KeyError, AttributeError):
            return None
    except TypeError:
        return None


def get_identifier_from_recipe_file(filename):
    """Attempts to read filename and get the
    identifier. Otherwise, returns None."""
    recipe_dict = recipe_from_file(filename)
    return get_identifier(recipe_dict)


def find_recipe_by_identifier(identifier, search_dirs):
    """Search search_dirs for a recipe with the given
    identifier"""
    for directory in search_dirs:
        # TODO: Combine with similar code in get_recipe_list() and find_recipe_by_name()
        normalized_dir = os.path.abspath(os.path.expanduser(directory))
        patterns = [os.path.join(normalized_dir, f"*{ext}") for ext in RECIPE_EXTS]
        patterns.extend(
            [os.path.join(normalized_dir, f"*/*{ext}") for ext in RECIPE_EXTS]
        )
        for pattern in patterns:
            matches = glob.glob(pattern)
            for match in matches:
                if get_identifier_from_recipe_file(match) == identifier:
                    return match

    return None


def valid_recipe_dict_with_keys(recipe_dict, keys_to_verify):
    """Attempts to read a dict and ensures the keys in
    keys_to_verify exist. Returns False on any failure, True otherwise."""
    if recipe_dict:
        for key in keys_to_verify:
            if key not in recipe_dict:
                return False
        # if we get here, we found all the keys
        return True
    return False


def valid_recipe_dict(recipe_dict):
    """Returns True if recipe dict is a valid recipe,
    otherwise returns False"""
    return (
        valid_recipe_dict_with_keys(recipe_dict, ["Input", "Process"])
        or valid_recipe_dict_with_keys(recipe_dict, ["Input", "Recipe"])
        or valid_recipe_dict_with_keys(recipe_dict, ["Input", "ParentRecipe"])
    )


def valid_override_dict(recipe_dict):
    """Returns True if the recipe is a valid override,
    otherwise returns False"""
    return valid_recipe_dict_with_keys(
        recipe_dict, ["Input", "ParentRecipe"]
    ) or valid_recipe_dict_with_keys(recipe_dict, ["Input", "Recipe"])


def valid_override_file(filename):
    """Returns True if filename contains a valid override,
    otherwise returns False"""
    override_dict = recipe_from_file(filename)
    return valid_override_dict(override_dict)


def get_recipe_list(
    override_dirs=None, search_dirs=None, augmented_list=False, show_all=False
):
    """Factor out the core of list_recipes for use in other functions"""
    override_dirs = override_dirs or get_override_dirs()
    search_dirs = search_dirs or get_search_dirs()

    recipes = []
    for directory in search_dirs:
        # TODO: Combine with similar code in find_recipe_by_name()
        # and find_recipe_by_identifier()
        normalized_dir = os.path.abspath(os.path.expanduser(directory))
        if not os.path.isdir(normalized_dir):
            continue

        # find all top-level recipes and recipes one level down
        patterns = [os.path.join(normalized_dir, f"*{ext}") for ext in RECIPE_EXTS]
        patterns.extend(
            [os.path.join(normalized_dir, f"*/*{ext}") for ext in RECIPE_EXTS]
        )

        for pattern in patterns:
            matches = glob.glob(pattern)
            for match in matches:
                recipe = recipe_from_file(match)
                if valid_recipe_dict(recipe):
                    recipe_name = os.path.basename(match)

                    recipe["Name"] = remove_recipe_extension(recipe_name)
                    recipe["Path"] = match

                    # If a top level "Identifier" key is not discovered,
                    # this will copy an IDENTIFIER key in the "Input"
                    # entry to the top level of the recipe dictionary.
                    if "Identifier" not in recipe:
                        identifier = get_identifier(recipe)
                        if identifier:
                            recipe["Identifier"] = identifier

                    recipes.append(recipe)

    for directory in override_dirs:
        normalized_dir = os.path.abspath(os.path.expanduser(directory))
        if not os.path.isdir(normalized_dir):
            continue
        patterns = [os.path.join(normalized_dir, f"*{ext}") for ext in RECIPE_EXTS]
        for pattern in patterns:
            matches = glob.glob(pattern)
            for match in matches:
                override = recipe_from_file(match)
                if valid_override_dict(override):
                    override_name = os.path.basename(match)

                    override["Name"] = remove_recipe_extension(override_name)
                    override["Path"] = match
                    override["IsOverride"] = True

                    if augmented_list and not show_all:
                        # If an override has the same Name as the ParentRecipe
                        # AND the override's ParentRecipe matches said
                        # recipe's Identifier, remove the ParentRecipe from the
                        # listing.
                        for recipe in recipes:
                            if recipe["Name"] == override["Name"] and recipe.get(
                                "Identifier"
                            ) == override.get("ParentRecipe"):
                                recipes.remove(recipe)

                    recipes.append(override)
    return recipes


def make_suggestions_for(search_name):
    """Suggest existing recipes with names similar to search name."""
    # trim extension from the end if it exists
    search_name = remove_recipe_extension(search_name)
    (search_name_base, search_name_ext) = os.path.splitext(search_name.lower())
    recipe_names = [os.path.splitext(item["Name"]) for item in get_recipe_list()]
    recipe_names = list(set(recipe_names))

    matches = []
    if len(search_name_base) > 3:
        matches = [
            "".join(item)
            for item in recipe_names
            if (
                search_name_base in item[0].lower()
                and search_name_ext in item[1].lower()
            )
        ]
    if search_name_ext:
        compare_names = [
            item[0].lower()
            for item in recipe_names
            if item[1].lower() == search_name_ext
        ]
    else:
        compare_names = [item[0].lower() for item in recipe_names]

    close_matches = difflib.get_close_matches(search_name_base, compare_names)
    if close_matches:
        matches.extend(
            [
                "".join(item)
                for item in recipe_names
                if ("".join(item) not in matches and item[0].lower() in close_matches)
            ]
        )
        if search_name_ext:
            matches = [
                item for item in matches if os.path.splitext(item)[1] == search_name_ext
            ]

    if len(matches) == 1:
        print(f"Maybe you meant {matches[0]}?")
    elif len(matches):
        print(f"Maybe you meant one of: {', '.join(matches)}?")


def find_recipe_by_name(name, search_dirs):
    """Search search_dirs for a recipe by file/directory naming rules"""
    # drop extension from the end of the name because we're
    # going to add it back on...
    name = remove_recipe_extension(name)
    # search by "Name", using file/directory hierarchy rules
    for directory in search_dirs:
        # TODO: Combine with similar code in get_recipe_list()
        # and find_recipe_by_identifier()
        normalized_dir = os.path.abspath(os.path.expanduser(directory))
        patterns = [os.path.join(normalized_dir, f"{name}{ext}") for ext in RECIPE_EXTS]
        patterns.extend(
            [os.path.join(normalized_dir, f"*/{name}{ext}") for ext in RECIPE_EXTS]
        )
        for pattern in patterns:
            matches = glob.glob(pattern)
            for match in matches:
                if valid_recipe_file(match):
                    return match

    return None


def find_recipe(id_or_name, search_dirs):
    """find a recipe based on a string that might be an identifier
    or a name"""
    return find_recipe_by_identifier(id_or_name, search_dirs) or find_recipe_by_name(
        id_or_name, search_dirs
    )


def get_identifier_from_override(override):
    """Return the identifier from an override, falling back with a
    warning to just the 'name' of the recipe."""
    # prefer ParentRecipe
    identifier = override.get("ParentRecipe")
    if identifier:
        return identifier
    identifier = override["Recipe"].get("identifier")
    if identifier:
        return identifier
    else:
        name = override["Recipe"].get("name")
        log_err(
            "WARNING: Override contains no identifier. Will fall "
            "back to matching it by name using search rules. It's "
            "recommended to give the original recipe identifier "
            "in the override's 'Recipes' dict to ensure the same "
            "recipe is always used for this override."
        )
    return name


def get_repository_from_identifier(identifier: str):
    """Get a repository name from a recipe identifier."""
    results = GitHubSession().search_for_name(identifier)
    # so now we have a list of items containing file names and URLs
    # we want to fetch these so we can look inside the contents for a matching
    # identifier
    # We just want to fetch the repos that contain these
    # Is the name an identifier?
    identifier_fragments = identifier.split(".")
    if identifier_fragments[0] != "com":
        # This is not an identifier
        return
    correct_item = None
    for item in results:
        file_contents_raw = do_gh_repo_contents_fetch(
            item["repository"]["name"], item.get("path")
        )
        file_contents_data = plistlib.loads(file_contents_raw)
        if file_contents_data.get("Identifier") == identifier:
            correct_item = item
            break
    # Did we get correct item?
    if not correct_item:
        return
    print(f"Found this recipe in repository: {correct_item['repository']['name']}")
    return correct_item["repository"]["name"]


def get_recipe_info(
    recipe_name,
    override_dirs,
    recipe_dirs,
    make_suggestions=True,
    search_github=True,
    auto_pull=False,
):
    """Loads a recipe, then prints some information about it. Override aware."""
    recipe = load_recipe(
        recipe_name,
        override_dirs,
        recipe_dirs,
        make_suggestions=make_suggestions,
        search_github=search_github,
        auto_pull=auto_pull,
    )
    if recipe:
        log(
            "Description:         {}".format(
                "\n                     ".join(
                    recipe.get("Description", "").splitlines()
                )
            )
        )
        log(f"Identifier:          {get_identifier(recipe)}")
        log(f"Munki import recipe: {has_munkiimporter_step(recipe)}")
        log(f"Has check phase:     {has_check_phase(recipe)}")
        log(f"Builds package:      {builds_a_package(recipe)}")
        log(f"Recipe file path:    {recipe['RECIPE_PATH']}")
        if recipe.get("PARENT_RECIPES"):
            log(
                "Parent recipe(s):    {}".format(
                    "\n                     ".join(recipe["PARENT_RECIPES"])
                )
            )
        log("Input values: ")
        output = pprint.pformat(recipe.get("Input", {}), indent=4)
        log(" " + output[1:-1])
        return True
    else:
        log_err(f"No valid recipe found for {recipe_name}")
        return False


def valid_recipe_file(filename):
    """Returns True if filename contains a valid recipe,
    otherwise returns False"""
    recipe_dict = recipe_from_file(filename)
    return valid_recipe_dict(recipe_dict)


def recipe_from_file(filename):
    """Create a recipe dictionary from a file. Handle exceptions and log"""
    if not os.path.isfile(filename):
        return

    if filename.endswith(".yaml"):
        try:
            # try to read it as yaml
            with open(filename, "rb") as f:
                recipe_dict = yaml.load(f, Loader=yaml.FullLoader)
            return recipe_dict
        except Exception as err:
            log_err(f"WARNING: yaml error for {filename}: {err}")
            return

    else:
        try:
            # try to read it as a plist
            with open(filename, "rb") as f:
                recipe_dict = plistlib.load(f)
            return recipe_dict
        except Exception as err:
            log_err(f"WARNING: plist error for {filename}: {err}")
            return


def locate_recipe(
    name,
    override_dirs,
    recipe_dirs,
    make_suggestions=True,
    search_github=True,
    auto_pull=False,
):
    """Locates a recipe by name. If the name is the pathname to a file on disk,
    we attempt to load that file and use it as recipe. If a parent recipe
    is required we first add the child recipe's directory to the search path
    so that the parent can be found, assuming it is in the same directory.

    Otherwise, we treat name as a recipe name or identifier and search first
    the override directories, then the recipe directories for a matching
    recipe."""

    recipe_file = None
    if os.path.isfile(name):
        # name is path to a specific recipe or override file
        # ignore override and recipe directories
        # and attempt to open the file specified by name
        if valid_recipe_file(name):
            recipe_file = name

    if not recipe_file:
        # name wasn't a filename. Let's search our local repos.
        recipe_file = find_recipe(name, override_dirs + recipe_dirs)

    if not recipe_file and make_suggestions:
        print(f"Didn't find a recipe for {name}.")
        make_suggestions_for(name)

    if not recipe_file and search_github:
        indef_article = "a"
        if name[0].lower() in ["a", "e", "i", "o", "u"]:
            indef_article = "an"
        if not auto_pull:
            answer = input(
                f"Search GitHub AutoPkg repos for {indef_article} {name} recipe? "
                "[y/n]: "
            )
        else:
            answer = "y"
        if answer.lower().startswith("y"):
            identifier_fragments = name.split(".")
            repo_names = []
            if identifier_fragments[0] == "com":
                # Filter out "None" results if we don't find a matching recipe
                parent_repo = get_repository_from_identifier(name)
                repo_names = [parent_repo] if parent_repo else []

            if not repo_names:
                results_items = GitHubSession().search_for_name(name)
                print_gh_search_results(results_items)
                # make a list of unique repo names
                repo_names = []
                for item in results_items:
                    repo_name = item["repository"]["name"]
                    if repo_name not in repo_names:
                        repo_names.append(repo_name)

            if len(repo_names) == 1:
                # we found results in a single repo, so offer to add it
                repo = repo_names[0]
                if not auto_pull:
                    print()
                    answer = input(f"Add recipe repo '{repo}'? [y/n]: ")
                else:
                    answer = "y"
                if answer.lower().startswith("y"):
                    # repo_add([None, "repo-add", repo]) # what the fuck do I do here?
                    # # try once again to locate the recipe, but don't
                    # # search GitHub again!
                    # print()
                    # recipe_dirs = get_search_dirs()
                    # recipe_file = locate_recipe(
                    #     name,
                    #     override_dirs,
                    #     recipe_dirs,
                    #     make_suggestions=True,
                    #     search_github=False,
                    #     auto_pull=auto_pull,
                    # )
                    print(
                        f"Use `autopkg repo-add {repo}` "
                        "to add this repo and run it again."
                    )
            elif len(repo_names) > 1:
                print()
                print("To add a new recipe repo, use 'autopkg repo-add " "<repo name>'")
                return None

    return recipe_file


def load_recipe(
    name,
    override_dirs,
    recipe_dirs,
    preprocessors=None,
    postprocessors=None,
    make_suggestions=True,
    search_github=True,
    auto_pull=False,
):
    """Loads a recipe, first locating it by name.
    If we find one, we load it and return the dictionary object. If an
    override file is used, it prefers finding the original recipe by
    identifier rather than name, so that if recipe names shift with
    updated recipe repos, the override still applies to the recipe from
    which it was derived."""

    if override_dirs is None:
        override_dirs = []
    if recipe_dirs is None:
        recipe_dirs = []
    recipe = None
    recipe_file = locate_recipe(
        name,
        override_dirs,
        recipe_dirs,
        make_suggestions=make_suggestions,
        search_github=search_github,
        auto_pull=auto_pull,
    )

    if recipe_file:
        # read it
        recipe = recipe_from_file(recipe_file)

        # store parent trust info, but only if this is an override
        if recipe_in_override_dir(recipe_file, override_dirs):
            parent_trust_info = recipe.get("ParentRecipeTrustInfo")
            override_parent = recipe.get("ParentRecipe") or recipe.get("Recipe")
        else:
            parent_trust_info = None

        # does it refer to another recipe?
        if recipe.get("ParentRecipe") or recipe.get("Recipe"):
            # save current recipe as a child
            child_recipe = recipe
            parent_id = get_identifier_from_override(recipe)
            # add the recipe's directory to the search path
            # so that we'll be able to locate the parent
            recipe_dirs.append(os.path.dirname(recipe_file))
            # load its parent, this time not looking in override directories
            recipe = load_recipe(
                parent_id,
                [],
                recipe_dirs,
                make_suggestions=make_suggestions,
                search_github=search_github,
                auto_pull=auto_pull,
            )
            if recipe:
                # merge child_recipe
                recipe["Identifier"] = get_identifier(child_recipe)
                recipe["Description"] = child_recipe.get(
                    "Description", recipe.get("Description", "")
                )
                for key in list(child_recipe["Input"].keys()):
                    recipe["Input"][key] = child_recipe["Input"][key]

                # take the highest of the two MinimumVersion keys, if they exist
                for candidate_recipe in [recipe, child_recipe]:
                    if "MinimumVersion" not in list(candidate_recipe.keys()):
                        candidate_recipe["MinimumVersion"] = "0"
                if version_equal_or_greater(
                    child_recipe["MinimumVersion"], recipe["MinimumVersion"]
                ):
                    recipe["MinimumVersion"] = child_recipe["MinimumVersion"]

                recipe["Process"].extend(child_recipe.get("Process", []))
                if recipe.get("RECIPE_PATH"):
                    if "PARENT_RECIPES" not in recipe:
                        recipe["PARENT_RECIPES"] = []
                    recipe["PARENT_RECIPES"] = [recipe["RECIPE_PATH"]] + recipe[
                        "PARENT_RECIPES"
                    ]
                recipe["RECIPE_PATH"] = recipe_file
            else:
                # no parent recipe, so the current recipe is invalid
                log_err(f"Could not find parent recipe for {name}")
        else:
            recipe["RECIPE_PATH"] = recipe_file

        # re-add original stored parent trust info or remove it if it was picked
        # up from a parent recipe
        if recipe:
            if parent_trust_info:
                recipe["ParentRecipeTrustInfo"] = parent_trust_info
                if override_parent:
                    recipe["ParentRecipe"] = override_parent
                else:
                    log_err(f"No parent recipe specified for {name}")
            elif "ParentRecipeTrustInfo" in recipe:
                del recipe["ParentRecipeTrustInfo"]

    if recipe:
        # store the name the user used to locate this recipe
        recipe["name"] = name

    if recipe and preprocessors:
        steps = []
        for preprocessor_name in preprocessors:
            steps.append({"Processor": preprocessor_name})
        steps.extend(recipe["Process"])
        recipe["Process"] = steps

    if recipe and postprocessors:
        steps = recipe["Process"]
        for postprocessor_name in postprocessors:
            steps.append({"Processor": postprocessor_name})
        recipe["Process"] = steps

    return recipe


def assemble_recipe(
    recipe_name, override_dirs, search_dirs, preprocessors=None, postprocessors=None
):
    """Assemble a recipe into a single object"""
    if not recipe_name:
        raise RecipeLoadingException(recipe_name)
    cache_dir = get_pref("CACHE_DIR") or "~/Library/AutoPkg/Cache"
    cache_dir = os.path.expanduser(cache_dir)
    if not os.path.exists(cache_dir):
        os.makedirs(cache_dir, 0o755)
    current_run_results_plist = os.path.join(cache_dir, "autopkg_results.plist")

    run_results = []
    try:
        with open(current_run_results_plist, "wb") as f:
            plistlib.dump(run_results, f)
    except OSError as err:
        log_err(f"Can't write results to {current_run_results_plist}: {err.strerror}")

    recipe = load_recipe(
        recipe_name,
        override_dirs,
        search_dirs,
        preprocessors,
        postprocessors,
        make_suggestions=False,
        search_github=False,
    )
    if not recipe:
        log_err(f"No valid recipe found for {recipe_name}")
        raise RecipeLoadingException(f"No valid recipe found for {recipe_name}")
    return recipe


def recipe_from_external_repo(recipe_path):
    """Returns True if the recipe_path is in a path in RECIPE_REPOS, which contains
    recipes added via repo-add"""
    recipe_repos = get_pref("RECIPE_REPOS") or {}
    for repo in list(recipe_repos.keys()):
        if recipe_path.startswith(repo):
            return True
    return False


def recipe_in_override_dir(recipe_path, override_dirs):
    """Returns True if the recipe is in a path in override_dirs"""
    normalized_recipe_path = os.path.abspath(os.path.expanduser(recipe_path))
    normalized_override_dirs = [
        os.path.abspath(os.path.expanduser(directory)) for directory in override_dirs
    ]
    for override_dir in normalized_override_dirs:
        if normalized_recipe_path.startswith(override_dir):
            return True
    return False
