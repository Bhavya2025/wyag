import argparse
import collections
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

# --- Chapter 4: Git objects ------------------------------------------------

class GitObject(object):
    def __init__(self, data=None):
        if data is not None:
            self.deserialize(data)
        else:
            self.init()

    def serialize(self, repo):
        raise Exception("Unimplemented!")

    def deserialize(self, data):
        raise Exception("Unimplemented!")

    def init(self):
        pass

def object_read(repo, sha):
    path = repo_file(repo, "objects", sha[0:2], sha[2:])

    if not os.path.isfile(path):
        return None

    with open(path, "rb") as f:
        raw = zlib.decompress(f.read())

        # Read object type
        x = raw.find(b' ')
        fmt = raw[0:x]

        # Read and validate object size
        y = raw.find(b'\x00', x)
        size = int(raw[x:y].decode("ascii"))
        if size != len(raw) - y - 1:
            raise Exception(f"Malformed object {sha}: bad length")

        # Pick constructor
        match fmt:
            case b'commit' : c = GitCommit
            case b'tree'   : c = GitTree
            case b'tag'    : c = GitTag
            case b'blob'   : c = GitBlob
            case _:
                raise Exception(f"Unknown type {fmt.decode('ascii')} for object {sha}")

        return c(raw[y + 1:])

def object_write(obj, repo=None):
    # Serialize object data
    data = obj.serialize()
    # Add header
    result = obj.fmt + b' ' + str(len(data)).encode() + b'\x00' + data
    # Compute hash
    sha = hashlib.sha1(result).hexdigest()

    if repo:
        path = repo_file(repo, "objects", sha[0:2], sha[2:], mkdir=True)

        if not os.path.exists(path):
            with open(path, 'wb') as f:
                f.write(zlib.compress(result))
    return sha

def object_find(repo, name, fmt=None, follow=True):
    return name

class GitBlob(GitObject):
    fmt = b'blob'

    def serialize(self):
        return self.blobdata

    def deserialize(self, data):
        self.blobdata = data

# --- Chapter 4.6: the cat-file command -------------------------------------

argsp = argsubparsers.add_parser("cat-file",
                                 help="Provide content of repository objects")
argsp.add_argument("type",
                   metavar="type",
                   choices=["blob", "commit", "tag", "tree"],
                   help="Specify the type")
argsp.add_argument("object",
                   metavar="object",
                   help="The object to display")

def cmd_cat_file(args):
    repo = repo_find()
    cat_file(repo, args.object, fmt=args.type.encode())

def cat_file(repo, obj, fmt=None):
    obj = object_read(repo, object_find(repo, obj, fmt=fmt))
    sys.stdout.buffer.write(obj.serialize())

# --- Chapter 4.7: the hash-object command ----------------------------------

argsp = argsubparsers.add_parser(
    "hash-object",
    help="Compute object ID and optionally creates a blob from a file")
argsp.add_argument("-t",
                   metavar="type",
                   dest="type",
                   choices=["blob", "commit", "tag", "tree"],
                   default="blob",
                   help="Specify the type")
argsp.add_argument("-w",
                   dest="write",
                   action="store_true",
                   help="Actually write the object into the database")
argsp.add_argument("path",
                   help="Read object from <file>")

def cmd_hash_object(args):
    if args.write:
        repo = repo_find()
    else:
        repo = None

    with open(args.path, "rb") as fd:
        sha = object_hash(fd, args.type.encode(), repo)
        print(sha)

def object_hash(fd, fmt, repo=None):
    data = fd.read()

    match fmt:
        case b'commit' : obj = GitCommit(data)
        case b'tree'   : obj = GitTree(data)
        case b'tag'    : obj = GitTag(data)
        case b'blob'   : obj = GitBlob(data)
        case _: raise Exception(f"Unknown type {fmt}!")

    return object_write(obj, repo)

# --- Chapter 5: Reading commit data ----------------------------------------

def kvlm_parse(raw, start=0, dct=None):
    # A commit (and a tag) is stored as a "Key-Value List with Message":
    # a few "key value" header lines, a blank line, then the free-form
    # message. We parse it one pair at a time, recursing on the rest.
    if not dct:
        dct = collections.OrderedDict()
        # Note: we must build a fresh dict here rather than using
        # dct=collections.OrderedDict() as a default argument. Default
        # arguments are evaluated ONCE at definition time, so every call
        # would share — and keep growing — the same dict.

    # Where does the next space and the next newline appear?
    spc = raw.find(b' ', start)
    nl = raw.find(b'\n', start)

    # Base case: a space either doesn't appear, or appears after the next
    # newline. That means this line is blank, and everything after it is
    # the message. We store it under the key None and stop recursing.
    if (spc < 0) or (nl < spc):
        assert nl == start
        dct[None] = raw[start + 1:]
        return dct

    # Recursive case: read one "key value" header.
    key = raw[start:spc]

    # A value can span multiple lines; continuation lines start with a
    # space. Keep scanning for the next '\n' until it is NOT followed by
    # a space — that is the real end of the value.
    end = start
    while True:
        end = raw.find(b'\n', end + 1)
        if raw[end + 1] != ord(' '):
            break

    # Grab the value and drop the leading space of each continuation line.
    value = raw[spc + 1:end].replace(b'\n ', b'\n')

    # A key can legitimately repeat (e.g. a merge commit has two parents),
    # so collect repeats into a list instead of overwriting.
    if key in dct:
        if type(dct[key]) == list:
            dct[key].append(value)
        else:
            dct[key] = [dct[key], value]
    else:
        dct[key] = value

    return kvlm_parse(raw, start=end + 1, dct=dct)

def kvlm_serialize(kvlm):
    ret = b''

    # Output the header fields, in insertion order.
    for k in kvlm.keys():
        # The message is stored under None; we append it last.
        if k == None:
            continue
        val = kvlm[k]
        # Normalize single values to a one-element list so we can loop.
        if type(val) != list:
            val = [val]

        for v in val:
            # Re-indent continuation lines to match git's on-disk format.
            ret += k + b' ' + (v.replace(b'\n', b'\n ')) + b'\n'

    # Blank line separating headers from the message, then the message.
    ret += b'\n' + kvlm[None] + b'\n'

    return ret

class GitCommit(GitObject):
    fmt = b'commit'

    def deserialize(self, data):
        self.kvlm = kvlm_parse(data)

    def serialize(self):
        return kvlm_serialize(self.kvlm)

    def init(self):
        self.kvlm = dict()

# --- Chapter 5.4: the log command ------------------------------------------

argsp = argsubparsers.add_parser("log", help="Display history of a given commit.")
argsp.add_argument("commit",
                   default="HEAD",
                   nargs="?",
                   help="Commit to start at.")

def cmd_log(args):
    repo = repo_find()

    print("digraph wyaglog{")
    print("  node[shape=rect]")
    log_graphviz(repo, object_find(repo, args.commit), set())
    print("}")

def log_graphviz(repo, sha, seen):
    # A commit can be reached by more than one path (merges), so track
    # what we've already drawn to avoid infinite loops and duplicates.
    if sha in seen:
        return
    seen.add(sha)

    commit = object_read(repo, sha)
    message = commit.kvlm[None].decode("utf8").strip()
    # Escape characters that would break the Graphviz label string.
    message = message.replace("\\", "\\\\")
    message = message.replace("\"", "\\\"")

    if "\n" in message:  # Keep only the first line of the message.
        message = message[:message.index("\n")]

    print("  c_{0} [label=\"{1}: {2}\"]".format(sha, sha[0:7], message))
    assert commit.fmt == b'commit'

    if not b'parent' in commit.kvlm.keys():
        # Base case: the very first commit has no parent.
        return

    parents = commit.kvlm[b'parent']

    if type(parents) != list:
        parents = [parents]

    for p in parents:
        p = p.decode("ascii")
        print("  c_{0} -> c_{1};".format(sha, p))
        log_graphviz(repo, p, seen)

# --- Chapter 6: Reading commit data: checkout ------------------------------

class GitTreeLeaf(object):
    # One entry in a tree: a file mode, a path (name), and the SHA of the
    # object (blob or sub-tree) it points to.
    def __init__(self, mode, path, sha):
        self.mode = mode
        self.path = path
        self.sha = sha

def tree_parse_one(raw, start=0):
    # A single entry is: [mode] space [path] 0x00 [20-byte raw SHA].
    # Parse exactly one and return where the next one begins.
    x = raw.find(b' ', start)
    assert x - start == 5 or x - start == 6

    # Read the mode EXACTLY as stored. Git writes directory modes with no
    # leading zero ("40000", 5 chars) and file modes with six ("100644").
    # We keep the raw bytes so that re-serializing reproduces git's bytes
    # verbatim and the tree hashes to the same SHA.
    mode = raw[start:x]

    # The path runs from after the space up to the NULL terminator.
    y = raw.find(b'\x00', x)
    path = raw[x + 1:y]

    # The next 20 bytes are the raw binary SHA. Turn it into the usual
    # 40-character lowercase hex string.
    sha = format(int.from_bytes(raw[y + 1:y + 21], "big"), "040x")
    return y + 21, GitTreeLeaf(mode, path.decode("utf8"), sha)

def tree_parse(raw):
    # A tree is just one entry after another with no separators, so we
    # walk the buffer until it is exhausted.
    pos = 0
    max = len(raw)
    ret = list()
    while pos < max:
        pos, data = tree_parse_one(raw, pos)
        ret.append(data)
    return ret

def tree_leaf_sort_key(leaf):
    # Git sorts tree entries by name, but treats directories as if their
    # name ended in "/". Matching this byte-for-byte is required or our
    # tree would hash differently from git's.
    if leaf.mode.startswith(b"10"):
        return leaf.path
    else:
        return leaf.path + "/"

def tree_serialize(obj):
    # Sort first (see above), then concatenate each entry back into the
    # exact on-disk byte format.
    obj.items.sort(key=tree_leaf_sort_key)
    ret = b''
    for i in obj.items:
        ret += i.mode
        ret += b' '
        ret += i.path.encode("utf8")
        ret += b'\x00'
        sha = int(i.sha, 16)
        ret += sha.to_bytes(20, byteorder="big")
    return ret

class GitTree(GitObject):
    fmt = b'tree'

    def deserialize(self, data):
        self.items = tree_parse(data)

    def serialize(self):
        return tree_serialize(self)

    def init(self):
        self.items = list()

# --- Chapter 6.3: the ls-tree command --------------------------------------

argsp = argsubparsers.add_parser("ls-tree", help="Pretty-print a tree object.")
argsp.add_argument("-r",
                   dest="recursive",
                   action="store_true",
                   help="Recurse into sub-trees")
argsp.add_argument("tree",
                   help="A tree-ish object.")

def cmd_ls_tree(args):
    repo = repo_find()
    ls_tree(repo, args.tree, args.recursive)

def ls_tree(repo, ref, recursive=None, prefix=""):
    sha = object_find(repo, ref, fmt=b"tree")
    obj = object_read(repo, sha)
    for item in obj.items:
        # The first two digits of the mode encode the object type. A 5-char
        # mode ("40000") is missing its leading zero, so pad a local copy
        # before slicing — we never mutate item.mode itself.
        if len(item.mode) == 5:
            type = b'0' + item.mode[0:1]
        else:
            type = item.mode[0:2]

        match type:
            case b'04': type = "tree"
            case b'10': type = "blob"    # A regular file.
            case b'12': type = "blob"    # A symlink; blob holds the target.
            case b'16': type = "commit"  # A submodule.
            case _: raise Exception("Weird tree leaf mode {}".format(item.mode))

        if not (recursive and type == 'tree'):  # A leaf: print it.
            print("{0} {1} {2}\t{3}".format(
                "0" * (6 - len(item.mode)) + item.mode.decode("ascii"),
                type,
                item.sha,
                os.path.join(prefix, item.path)))
        else:  # A sub-tree and -r was given: recurse into it.
            ls_tree(repo, item.sha, recursive, os.path.join(prefix, item.path))

# --- Chapter 6.4: the checkout command -------------------------------------

argsp = argsubparsers.add_parser("checkout",
                                 help="Checkout a commit inside of a directory.")
argsp.add_argument("commit",
                   help="The commit or tree to checkout.")
argsp.add_argument("path",
                   help="The EMPTY directory to checkout on.")

def cmd_checkout(args):
    repo = repo_find()

    obj = object_read(repo, object_find(repo, args.commit))

    # If we were handed a commit, follow it to its tree.
    if obj.fmt == b'commit':
        obj = object_read(repo, obj.kvlm[b'tree'].decode("ascii"))

    # Refuse to clobber: the target must be an empty directory (or absent).
    if os.path.exists(args.path):
        if not os.path.isdir(args.path):
            raise Exception("Not a directory {0}!".format(args.path))
        if os.listdir(args.path):
            raise Exception("Not empty {0}!".format(args.path))
    else:
        os.makedirs(args.path)

    tree_checkout(repo, obj, os.path.realpath(args.path))

def tree_checkout(repo, tree, path):
    # Recreate every entry of the tree on the filesystem, recursing into
    # sub-trees as directories and writing blobs as files.
    for item in tree.items:
        obj = object_read(repo, item.sha)
        dest = os.path.join(path, item.path)

        if obj.fmt == b'tree':
            os.mkdir(dest)
            tree_checkout(repo, obj, dest)
        elif obj.fmt == b'blob':
            # @TODO Support symlinks (mode 12****).
            with open(dest, 'wb') as f:
                f.write(obj.blobdata)