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

def object_resolve(repo, name):
    """Resolve a name to one or more candidate object hashes. Understands
    HEAD, full and short hashes, tags, and branches."""
    candidates = list()
    hashRE = re.compile(r"^[0-9A-Fa-f]{4,40}$")

    if not name.strip():
        return None

    # HEAD is unambiguous.
    if name == "HEAD":
        return [ref_resolve(repo, "HEAD")]

    # A hex string might be a full or short hash.
    if hashRE.match(name):
        name = name.lower()
        prefix = name[0:2]
        path = repo_dir(repo, "objects", prefix, mkdir=False)
        if path:
            rem = name[2:]
            # Any object whose name starts with our prefix is a match;
            # a full hash matches exactly one, a short hash maybe several.
            for f in os.listdir(path):
                if f.startswith(rem):
                    candidates.append(prefix + f)

    # It could also be a tag...
    as_tag = ref_resolve(repo, "refs/tags/" + name)
    if as_tag:
        candidates.append(as_tag)

    # ...or a branch.
    as_branch = ref_resolve(repo, "refs/heads/" + name)
    if as_branch:
        candidates.append(as_branch)

    return candidates

def object_find(repo, name, fmt=None, follow=True):
    sha = object_resolve(repo, name)

    if not sha:
        raise Exception("No such reference {0}.".format(name))

    if len(sha) > 1:
        raise Exception("Ambiguous reference {0}: Candidates are:\n - {1}.".format(
            name, "\n - ".join(sha)))

    sha = sha[0]

    if not fmt:
        return sha

    # A type was requested. Follow the chain (tag -> object it tags, or
    # commit -> its tree) until we reach the requested type or give up.
    while True:
        obj = object_read(repo, sha)

        if obj.fmt == fmt:
            return sha

        if not follow:
            return None

        if obj.fmt == b'tag':
            sha = obj.kvlm[b'object'].decode("ascii")
        elif obj.fmt == b'commit' and fmt == b'tree':
            sha = obj.kvlm[b'tree'].decode("ascii")
        else:
            return None

# --- Chapter 8: the rev-parse command --------------------------------------

argsp = argsubparsers.add_parser("rev-parse",
                                 help="Parse revision (or other objects) identifiers")
argsp.add_argument("--wyag-type",
                   metavar="type",
                   dest="type",
                   choices=["blob", "commit", "tag", "tree"],
                   default=None,
                   help="Specify the expected type")
argsp.add_argument("name",
                   help="The name to parse")

def cmd_rev_parse(args):
    if args.type:
        fmt = args.type.encode()
    else:
        fmt = None
    repo = repo_find()
    print(object_find(repo, args.name, fmt, follow=True))

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

# --- Chapter 7: References, tags and branches ------------------------------

def ref_resolve(repo, ref):
    # A ref is a tiny file under .git that holds either a SHA, or a line
    # "ref: <another ref>" pointing somewhere else (an indirect ref).
    path = repo_file(repo, ref)

    # An indirect ref can be legitimately broken: a brand-new repo has
    # HEAD -> refs/heads/master, but that file doesn't exist until the
    # first commit. Treat a missing target as "unresolved".
    if not os.path.isfile(path):
        return None

    with open(path, 'r') as fp:
        data = fp.read()[:-1]  # strip the trailing newline

    if data.startswith("ref: "):
        return ref_resolve(repo, data[5:])  # follow the indirection
    else:
        return data  # a direct SHA

def ref_list(repo, path=None):
    # Walk .git/refs and build a nested dict mirroring the directory tree:
    # directories become sub-dicts, ref files become their resolved SHA.
    if not path:
        path = repo_dir(repo, "refs")
    ret = collections.OrderedDict()
    # Sort so output is deterministic and matches git's ordering.
    for f in sorted(os.listdir(path)):
        can = os.path.join(path, f)
        if os.path.isdir(can):
            ret[f] = ref_list(repo, can)
        else:
            ret[f] = ref_resolve(repo, can)

    return ret

# --- Chapter 7.1: the show-ref command -------------------------------------

argsp = argsubparsers.add_parser("show-ref", help="List references.")

def cmd_show_ref(args):
    repo = repo_find()
    refs = ref_list(repo)
    show_ref(repo, refs, prefix="refs")

def show_ref(repo, refs, with_hash=True, prefix=""):
    for k, v in refs.items():
        if type(v) == str:
            print("{0}{1}{2}".format(
                v + " " if with_hash else "",
                prefix + "/" if prefix else "",
                k))
        else:
            show_ref(repo, v, with_hash=with_hash,
                     prefix="{0}{1}{2}".format(prefix, "/" if prefix else "", k))

# --- Chapter 7.3: tags -----------------------------------------------------

class GitTag(GitCommit):
    # A tag OBJECT shares the commit's KVLM format exactly, so we just
    # reuse GitCommit's serialize/deserialize and only change the type.
    fmt = b'tag'

argsp = argsubparsers.add_parser("tag", help="List and create tags")
argsp.add_argument("-a",
                   action="store_true",
                   dest="create_tag_object",
                   help="Whether to create a tag object")
argsp.add_argument("name",
                   nargs="?",
                   help="The new tag's name")
argsp.add_argument("object",
                   default="HEAD",
                   nargs="?",
                   help="The object the new tag will point to")

def cmd_tag(args):
    repo = repo_find()
    if args.name:
        tag_create(repo,
                   args.name,
                   args.object,
                   create_tag_object=args.create_tag_object)
    else:
        # No name given: just list existing tags.
        refs = ref_list(repo)
        show_ref(repo, refs["tags"], with_hash=False)

def tag_create(repo, name, ref, create_tag_object=False):
    # Resolve the target object first.
    sha = object_find(repo, ref)

    if create_tag_object:
        # Annotated tag: a real object holding metadata + a message, with
        # a ref pointing at it.
        tag = GitTag()
        tag.kvlm = collections.OrderedDict()
        tag.kvlm[b'object'] = sha.encode()
        tag.kvlm[b'type'] = b'commit'
        tag.kvlm[b'tag'] = name.encode()
        tag.kvlm[b'tagger'] = b'Wyag <wyag@example.com>'
        tag.kvlm[None] = b"A tag generated by wyag, which won't let you customize the message!\n"
        tag_sha = object_write(tag, repo)
        # The ref points to the tag object, not the target directly.
        ref_create(repo, "tags/" + name, tag_sha)
    else:
        # Lightweight tag: just a ref that points straight at the object.
        ref_create(repo, "tags/" + name, sha)

def ref_create(repo, ref_name, sha):
    with open(repo_file(repo, "refs/" + ref_name), 'w') as fp:
        fp.write(sha + "\n")

# --- Chapter 9: The staging area and the index -----------------------------

class GitIndexEntry(object):
    def __init__(self, ctime=None, mtime=None, dev=None, ino=None,
                 mode_type=None, mode_perms=None, uid=None, gid=None,
                 fsize=None, sha=None, flag_assume_valid=None,
                 flag_stage=None, name=None):
        self.ctime = ctime              # (seconds, nanoseconds) metadata change
        self.mtime = mtime              # (seconds, nanoseconds) data change
        self.dev = dev                  # device id
        self.ino = ino                  # inode number
        self.mode_type = mode_type      # 0b1000 file, 0b1010 symlink, 0b1110 gitlink
        self.mode_perms = mode_perms    # permission bits
        self.uid = uid                  # owner user id
        self.gid = gid                  # owner group id
        self.fsize = fsize              # file size in bytes
        self.sha = sha                  # the blob's SHA
        self.flag_assume_valid = flag_assume_valid
        self.flag_stage = flag_stage
        self.name = name                # full path, relative to worktree

class GitIndex(object):
    version = None
    entries = []

    def __init__(self, version=2, entries=None):
        if not entries:
            entries = list()
        self.version = version
        self.entries = entries

def index_read(repo):
    index_file = repo_file(repo, "index")

    # A brand-new repository has no index yet.
    if not os.path.exists(index_file):
        return GitIndex()

    with open(index_file, 'rb') as f:
        raw = f.read()

    # 12-byte header: "DIRC" magic, version, entry count.
    header = raw[:12]
    signature = header[:4]
    assert signature == b"DIRC"  # "DirCache"
    version = int.from_bytes(header[4:8], "big")
    assert version == 2, "wyag only supports index file version 2"
    count = int.from_bytes(header[8:12], "big")

    entries = list()
    content = raw[12:]
    idx = 0
    for i in range(0, count):
        # Each entry is a fixed 62-byte record followed by a NUL-terminated
        # name, padded so the next entry starts on an 8-byte boundary.
        ctime_s = int.from_bytes(content[idx: idx + 4], "big")
        ctime_ns = int.from_bytes(content[idx + 4: idx + 8], "big")
        mtime_s = int.from_bytes(content[idx + 8: idx + 12], "big")
        mtime_ns = int.from_bytes(content[idx + 12: idx + 16], "big")
        dev = int.from_bytes(content[idx + 16: idx + 20], "big")
        ino = int.from_bytes(content[idx + 20: idx + 24], "big")
        unused = int.from_bytes(content[idx + 24: idx + 26], "big")
        assert 0 == unused
        mode = int.from_bytes(content[idx + 26: idx + 28], "big")
        mode_type = mode >> 12
        assert mode_type in [0b1000, 0b1010, 0b1110]
        mode_perms = mode & 0b0000000111111111
        uid = int.from_bytes(content[idx + 28: idx + 32], "big")
        gid = int.from_bytes(content[idx + 32: idx + 36], "big")
        fsize = int.from_bytes(content[idx + 36: idx + 40], "big")
        sha = format(int.from_bytes(content[idx + 40: idx + 60], "big"), "040x")
        flags = int.from_bytes(content[idx + 60: idx + 62], "big")
        flag_assume_valid = (flags & 0b1000000000000000) != 0
        flag_extended = (flags & 0b0100000000000000) != 0
        assert not flag_extended
        flag_stage = flags & 0b0011000000000000
        # The bottom 12 bits hold the name length, capped at 0xFFF.
        name_length = flags & 0b0000111111111111

        idx += 62

        if name_length < 0xFFF:
            assert content[idx + name_length] == 0x00
            raw_name = content[idx:idx + name_length]
            idx += name_length + 1
        else:
            # Names longer than 0xFFF: scan for the terminating NUL.
            print("Notice: Name is 0x{:X} bytes long.".format(name_length))
            null_idx = content.find(b'\x00', idx + 0xFFF)
            raw_name = content[idx: null_idx]
            idx = null_idx + 1

        name = raw_name.decode("utf8")

        # Skip padding to the next 8-byte boundary.
        idx = 8 * ceil(idx / 8)

        entries.append(GitIndexEntry(ctime=(ctime_s, ctime_ns),
                                     mtime=(mtime_s, mtime_ns),
                                     dev=dev, ino=ino,
                                     mode_type=mode_type,
                                     mode_perms=mode_perms,
                                     uid=uid, gid=gid,
                                     fsize=fsize, sha=sha,
                                     flag_assume_valid=flag_assume_valid,
                                     flag_stage=flag_stage,
                                     name=name))

    return GitIndex(version=version, entries=entries)

# --- Chapter 9.3: the ls-files command -------------------------------------

argsp = argsubparsers.add_parser("ls-files", help="List all the staged files")
argsp.add_argument("--verbose", action="store_true", help="Show everything.")

def cmd_ls_files(args):
    repo = repo_find()
    index = index_read(repo)
    if args.verbose:
        print("Index file format v{}, containing {} entries.".format(
            index.version, len(index.entries)))

    for e in index.entries:
        print(e.name)
        if args.verbose:
            entry_type = {0b1000: "regular file",
                          0b1010: "symlink",
                          0b1110: "git link"}[e.mode_type]
            print("  {} with perms: {:o}".format(entry_type, e.mode_perms))
            print("  on blob: {}".format(e.sha))
            print("  created: {}.{}, modified: {}.{}".format(
                datetime.fromtimestamp(e.ctime[0]), e.ctime[1],
                datetime.fromtimestamp(e.mtime[0]), e.mtime[1]))
            print("  device: {}, inode: {}".format(e.dev, e.ino))
            print("  user: {} ({})  group: {} ({})".format(
                pwd.getpwuid(e.uid).pw_name, e.uid,
                grp.getgrgid(e.gid).gr_name, e.gid))
            print("  flags: stage={} assume_valid={}".format(
                e.flag_stage, e.flag_assume_valid))