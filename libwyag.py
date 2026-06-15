import argparse
import configparser
from datetime import datetime
try:
    import grp, pwd
except ModuleNotFoundError:
    pass
from fnmatch import fnmatch
import hashlib

from math import ceil
import os
import re
import sys
import zlib

argparser = argparse.ArgumentParser(description="The stupidest content tracker")

argsubparsers = argparser.add_subparsers(title="Commands", dest="command")
argsubparsers.required = True


def main(argv=sys.argv[1:]):
    args = argparser.parse_args(argv)
    match args.command:
        case "add"          : cmd_add(args)
        case "cat-file"     : cmd_cat_file(args)
        case "check-ignore" : cmd_check_ignore(args)
        case "checkout"     : cmd_checkout(args)
        case "commit"       : cmd_commit(args)
        case "hash-object"  : cmd_hash_object(args)
        case "init"         : cmd_init(args)
        case "log"          : cmd_log(args)
        case "ls-files"     : cmd_ls_files(args)
        case "ls-tree"      : cmd_ls_tree(args)
        case "rev-parse"    : cmd_rev_parse(args)
        case "rm"           : cmd_rm(args)
        case "show-ref"     : cmd_show_ref(args)
        case "status"       : cmd_status(args)
        case "tag"          : cmd_tag(args)
        case _              : print("Bad command.")

def repo_path(repo, *args):
    return os.path.join(repo.gitdir, *args)

def repo_dir(repo, *args, mkdir = False):
    current_path = repo_path(repo,*args)
    if (os.path.exists(current_path)):
        if (os.path.isdir(current_path)):
            return current_path
        else:
            raise TypeError(f"Not a directory {current_path}")
    elif(mkdir == True):
        os.makedirs(current_path)
        return current_path
    else:
        return None

def repo_file(repo, *path, mkdir=False):
    if (repo_dir(repo,*(path[:-1]), mkdir=mkdir) is not None):
        return repo_path(repo,*path)
    else:
        return None

def repo_default_config():
    ret = configparser.ConfigParser()
    ret.add_section("core")
    ret.set("core","repositoryformatversion","0")
    ret.set("core","filemode","false")
    ret.set("core","bare","false")
    return ret

class GitRepository(object):
    def __init__(self, path, force=False):
        self.worktree = path
        self.gitdir = os.path.join(path,".git")
        if (os.path.isdir(self.gitdir) is False and force is False):
            raise Exception(f"Not a git repository {path}")
        self.conf = configparser.ConfigParser()
        rep = repo_file(self,"config")
        if (rep and os.path.isfile(rep)):
            self.conf.read([rep])
        elif (force is False):
            raise Exception("Config File Missing")
        if not force:
            version = int(self.conf.get("core", "repositoryformatversion"))
            if version != 0:
                raise Exception(f"Unsupported repositoryformatversion: {version}")

def repo_create(path):
    repo = GitRepository(path, force = True)
    if os.path.exists(repo.worktree):
        if not os.path.isdir(repo.worktree):
            raise Exception(f"{path} is not a directory!")
        if os.path.exists(repo.gitdir) and os.listdir(repo.gitdir):
            raise Exception(f"{path} is not empty!")
    else:
        os.makedirs(repo.worktree)
    repo_dir(repo, "branches", mkdir=True)
    repo_dir(repo, "objects", mkdir=True)
    repo_dir(repo, "refs", "tags", mkdir=True)
    repo_dir(repo, "refs", "heads", mkdir=True)

    with open(repo_file(repo, "config"), "w") as f:
        config = repo_default_config()
        config.write(f)
    
    with open(repo_file(repo, "description"), "w") as f:
        f.write("Unnamed repository; edit this file 'description' to name the repository.\n")
    
    with open(repo_file(repo, "HEAD"), "w") as f:
        f.write("ref: refs/heads/master\n")
    return repo

# --- Chapter 3.2: the `init` command ---------------------------------------

argsp = argsubparsers.add_parser("init", help="Initialize a new, empty repository.")
argsp.add_argument("path",
                   metavar="directory",
                   nargs="?",
                   default=".",
                   help="Where to create the repository.")

def cmd_init(args):
    repo_create(args.path)

# --- Chapter 3.3: finding the repository root ------------------------------

def repo_find(path=".", required=True):
    path = os.path.realpath(path)

    if os.path.isdir(os.path.join(path, ".git")):
        return GitRepository(path)

    parent = os.path.realpath(os.path.join(path, ".."))

    if parent == path:
        # parent == path means we hit the filesystem root "/"
        if required:
            raise Exception("No git directory.")
        else:
            return None

    return repo_find(parent, required)