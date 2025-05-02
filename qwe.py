#!/usr/bin/env python3

import os
import sys
import argparse
import json
import datetime
import shutil
import tarfile
import subprocess
import re
import stat
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple, TypedDict, NoReturn

# --- Type Definitions ---
# Using TypedDict for better structure of the project dictionary
class ProjectDict(TypedDict, total=False):
    name: str
    created_at: str
    lang: str
    state: str # 'active', 'archived', 'deleted'
    path: Optional[str] # Relative path from repo root (None if archived and not kept)
    original_path: Optional[str] # Stored during remove/archive for recovery
    archive_path: Optional[str] # Relative path to the archive file

class IndexDict(TypedDict):
    repo_name: str
    created_at: str
    projects: List[ProjectDict]

# --- Constants ---
QWE_DIR_NAME = ".qwe"
INDEX_FILE_NAME = "index.json"
TEMPLATES_DIR_NAME = "templates"
TRASH_DIR_NAME = ".trash"
ARCHIVES_DIR_NAME = ".archives"
DATE_FORMAT = "%Y-%m-%dT%H:%M:%S" # ISO 8601 format for UTC time
SCRIPT_NAME = "qwe" # Name for the installed executable
VERSION = "0.2.1" # Updated version

# --- Default Gitignore Templates ---
DEFAULT_GITIGNORE = """
# Default ignore rules added by qwe
*.log
*.tmp
*~
.DS_Store
*.swp
"""

PYTHON_GITIGNORE = """
# Python specific rules added by qwe
__pycache__/
*.py[cod]
*$py.class

# Distribution / packaging
.Python
build/
develop-eggs/
dist/
downloads/
eggs/
.eggs/
lib/
lib64/
parts/
sdist/
var/
wheels/
pip-wheel-metadata/
share/python-wheels/
*.egg-info/
.installed.cfg
*.egg
MANIFEST

# Environments
.env
.venv
env/
venv/
ENV/

# IDEs / Editors
.vscode/
.idea/
*.sublime-*

# Testing
.pytest_cache/
.tox/
.coverage*
htmlcov/

# Other
*.log
*.tmp
*~
.DS_Store
*.swp
"""

# --- Custom Exceptions ---
class QweError(Exception):
    """Base exception for qwe specific errors."""
    pass

class RepoNotFoundError(QweError):
    """Raised when a qwe repository root cannot be found."""
    pass

class ProjectNotFoundError(QweError):
    """Raised when a project is not found in the index."""
    pass

class InvalidStateError(QweError):
    """Raised when an operation is attempted on a project in an invalid state."""
    pass

class FilesystemError(QweError):
    """Raised for file system operation failures."""
    pass

class GitError(QweError):
    """Raised for Git command failures."""
    pass

# --- Helper Functions ---

def exit_with_error(message: str, exit_code: int = 1) -> NoReturn:
    """Prints an error message to stderr and exits."""
    print(f"Error: {message}", file=sys.stderr)
    sys.exit(exit_code)

def find_repo_root(start_path: Path = Path(".")) -> Optional[Path]:
    """
    Searches upward from start_path for the .qwe directory.

    Args:
        start_path: The directory to start searching from.

    Returns:
        The Path object of the repository root if found, otherwise None.
    """
    current_path = start_path.resolve() # Get absolute path
    while True:
        qwe_path = current_path / QWE_DIR_NAME
        if qwe_path.is_dir():
            return current_path
        parent_path = current_path.parent
        if parent_path == current_path:  # Reached root directory
            return None
        current_path = parent_path

def _get_qwe_paths(repo_root: Path) -> Dict[str, Path]:
    """Gets the standard paths within the .qwe repository structure."""
    return {
        "qwe_dir": repo_root / QWE_DIR_NAME,
        "index": repo_root / QWE_DIR_NAME / INDEX_FILE_NAME,
        "templates": repo_root / QWE_DIR_NAME / TEMPLATES_DIR_NAME,
        "trash": repo_root / TRASH_DIR_NAME,
        "archives": repo_root / ARCHIVES_DIR_NAME,
    }

def load_index(repo_root: Path) -> IndexDict:
    """
    Loads the index.json file.

    Args:
        repo_root: The root path of the repository.

    Returns:
        The loaded index data as a dictionary.

    Raises:
        RepoNotFoundError: If the index file doesn't exist.
        QweError: If the file cannot be read or parsed.
    """
    paths = _get_qwe_paths(repo_root)
    index_path = paths["index"]
    if not index_path.is_file():
        raise RepoNotFoundError(f"Index file not found at {index_path}")
    try:
        with index_path.open('r', encoding='utf-8') as f:
            data = json.load(f)
            # Basic validation of top-level structure
            if not isinstance(data, dict) or "projects" not in data or not isinstance(data["projects"], list):
                 raise QweError(f"Invalid JSON format in {index_path}. Missing or invalid 'projects' list.")
            return data # Type checking is limited here, relies on TypedDict hints
    except json.JSONDecodeError as e:
        raise QweError(f"Could not decode JSON from {index_path}: {e}")
    except IOError as e:
        raise QweError(f"Error reading index file {index_path}: {e}")
    except Exception as e:
        raise QweError(f"Unexpected error loading index {index_path}: {e}")

def save_index(repo_root: Path, index_data: IndexDict) -> None:
    """
    Saves the index_data to index.json.

    Args:
        repo_root: The root path of the repository.
        index_data: The index data dictionary to save.

    Raises:
        QweError: If the file cannot be written.
    """
    paths = _get_qwe_paths(repo_root)
    index_path = paths["index"]
    try:
        # Ensure parent directory exists (should normally, but good practice)
        index_path.parent.mkdir(parents=True, exist_ok=True)
        # Write atomically: write to temp file then rename
        temp_path = index_path.with_suffix(f"{index_path.suffix}.tmp")
        with temp_path.open('w', encoding='utf-8') as f:
            json.dump(index_data, f, indent=2, ensure_ascii=False)
        temp_path.replace(index_path) # Atomic rename/replace
    except IOError as e:
        raise QweError(f"Error writing index file {index_path}: {e}")
    except Exception as e:
        # Clean up temp file if rename failed
        if temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass # Ignore cleanup error
        raise QweError(f"Unexpected error saving index {index_path}: {e}")

def get_project(index_data: IndexDict, project_name: str) -> Optional[ProjectDict]:
    """
    Finds a project by name in the index data.

    Args:
        index_data: The repository index data.
        project_name: The name of the project to find.

    Returns:
        The project dictionary if found, otherwise None.
    """
    for project in index_data.get("projects", []):
        # Ensure project is a dict and has a name before comparing
        if isinstance(project, dict) and project.get("name") == project_name:
            return project
    return None

def validate_name(name: str) -> bool:
    """
    Checks if a name is valid (no spaces, slashes, or starting with '.').

    Args:
        name: The name to validate.

    Returns:
        True if valid, False otherwise (prints error).
    """
    # Regex explanation:
    # \s : whitespace
    # /  : forward slash
    # \\ : backslash (needs escaping)
    # |  : OR
    if not name or re.search(r"[\s/\\]", name) or name.startswith('.'):
        print(f"Error: Invalid name '{name}'. Cannot contain spaces, slashes, or start with '.'", file=sys.stderr)
        return False
    return True

def get_git_info(project_path: Path) -> Dict[str, str]:
    """
    Gets the current git branch and last commit hash for a project.

    Args:
        project_path: The absolute path to the project directory.

    Returns:
        A dictionary with 'branch' and 'last_commit' keys. Values are 'N/A' if
        git info cannot be retrieved.
    """
    git_info = {"branch": "N/A", "last_commit": "N/A"}
    git_dir = project_path / ".git"

    if not git_dir.is_dir():
        return git_info # Not a git repo or not initialized yet

    try:
        # Use 'git symbolic-ref' for branch, fallback to 'rev-parse' for detached HEAD
        result_branch = subprocess.run(
            ["git", "symbolic-ref", "--short", "HEAD"],
            cwd=project_path, capture_output=True, text=True, check=False, encoding='utf-8', errors='ignore'
        )
        if result_branch.returncode == 0 and result_branch.stdout.strip():
            git_info["branch"] = result_branch.stdout.strip()
        else:
            # Try getting detached HEAD commit hash
            result_head = subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"],
                cwd=project_path, capture_output=True, text=True, check=False, encoding='utf-8', errors='ignore'
            )
            if result_head.returncode == 0 and result_head.stdout.strip():
                git_info["branch"] = f"detached HEAD ({result_head.stdout.strip()})"
            # else: keep N/A

        # Get last commit hash (short)
        result_commit = subprocess.run(
            ["git", "log", "-1", "--pretty=format:%h"],
            cwd=project_path, capture_output=True, text=True, check=False, encoding='utf-8', errors='ignore'
        )
        if result_commit.returncode == 0 and result_commit.stdout.strip():
            git_info["last_commit"] = result_commit.stdout.strip()

    except FileNotFoundError:
        # Only print warning once per run? Could use a global flag or state.
        # For now, print each time it's encountered.
        print("Warning: 'git' command not found. Cannot fetch Git info.", file=sys.stderr)
    except subprocess.CalledProcessError as e:
         print(f"Warning: Git command failed in {project_path}: {e}", file=sys.stderr)
    except Exception as e:
        # Catch unexpected errors during git interaction
        print(f"Warning: Error getting Git info for {project_path}: {e}", file=sys.stderr)

    return git_info

def get_timestamp() -> str:
    """Returns the current timestamp in the standard ISO 8601 format (UTC)."""
    return datetime.datetime.now(datetime.timezone.utc).strftime(DATE_FORMAT) + "Z"

def get_user_confirmation(prompt: str) -> bool:
    """Asks the user for confirmation."""
    try:
        confirm = input(f"{prompt} (y/N): ")
        return confirm.lower() == 'y'
    except EOFError: # Handle cases where input stream is closed unexpectedly
        return False


def safe_rmtree(path: Path) -> None:
    """Remove a directory tree, raising FilesystemError on failure."""
    try:
        if path.is_dir(): # Only try to remove if it's a directory
             shutil.rmtree(path)
        elif path.exists(): # If it exists but isn't a dir (e.g., file, symlink)
             path.unlink()
    except OSError as e:
        raise FilesystemError(f"Error removing {path}: {e}")
    except Exception as e:
        raise FilesystemError(f"Unexpected error removing {path}: {e}")

def safe_move(src: Path, dst: Path) -> None:
    """Move a file or directory, raising FilesystemError on failure."""
    try:
        shutil.move(str(src), str(dst)) # shutil.move needs strings
    except OSError as e:
        raise FilesystemError(f"Error moving {src} to {dst}: {e}")
    except Exception as e:
        raise FilesystemError(f"Unexpected error moving {src} to {dst}: {e}")

def safe_makedirs(path: Path) -> None:
    """Create directories, raising FilesystemError on failure."""
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise FilesystemError(f"Error creating directory {path}: {e}")
    except Exception as e:
        raise FilesystemError(f"Unexpected error creating directory {path}: {e}")

def safe_write_file(path: Path, content: str) -> None:
    """Write content to a file, raising FilesystemError on failure."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True) # Ensure parent dir exists
        path.write_text(content, encoding='utf-8')
    except IOError as e:
        raise FilesystemError(f"Error writing file {path}: {e}")
    except Exception as e:
        raise FilesystemError(f"Unexpected error writing file {path}: {e}")

def safe_read_file(path: Path) -> str:
    """Read content from a file, raising FilesystemError on failure."""
    try:
        return path.read_text(encoding='utf-8')
    except IOError as e:
        raise FilesystemError(f"Error reading file {path}: {e}")
    except Exception as e:
        raise FilesystemError(f"Unexpected error reading file {path}: {e}")

def find_executable_path(name: str) -> Optional[Path]:
    """Finds the full path of an executable in standard user bin directories."""
    home_dir = Path.home()
    # Common user bin locations on Linux/macOS
    possible_dirs = [
        home_dir / ".local" / "bin",
        home_dir / "bin"
    ]
    for directory in possible_dirs:
        executable_path = directory / name
        # Check if it's a file and executable
        if executable_path.is_file() and os.access(executable_path, os.X_OK):
            return executable_path
    return None

# --- Core Logic Functions ---

def _init_repo_structure(repo_root: Path, repo_name: str) -> None:
    """Initializes the directory structure and index file for a new repo."""
    paths = _get_qwe_paths(repo_root)
    try:
        for dir_path in [paths["qwe_dir"], paths["templates"], paths["trash"], paths["archives"]]:
            safe_makedirs(dir_path)

        # Create index.json
        index_data: IndexDict = {
            "repo_name": repo_name,
            "created_at": get_timestamp(),
            "projects": []
        }
        save_index(repo_root, index_data)

        # Create default .gitignore templates
        safe_write_file(paths["templates"] / "default.gitignore", DEFAULT_GITIGNORE)
        safe_write_file(paths["templates"] / "python.gitignore", PYTHON_GITIGNORE)

    except FilesystemError as e:
        # Use exit_with_error for consistent error handling
        # Attempt cleanup first
        if repo_root.name == repo_name: # Only if we created a new dir for the repo
             print(f"Attempting cleanup of {repo_root}...", file=sys.stderr)
             try:
                 safe_rmtree(repo_root)
             except FilesystemError as cleanup_e:
                 print(f"Error during cleanup: {cleanup_e}", file=sys.stderr)
        exit_with_error(f"Failed to initialize repository structure: {e}")


def _create_project_files(repo_root: Path, project_path: Path, project_name: str, lang: Optional[str]) -> None:
    """Creates README, .gitignore, and initializes Git for a new project."""
    paths = _get_qwe_paths(repo_root)

    # Add README.md
    try:
        safe_write_file(project_path / "README.md", f"# {project_name}\n\nCreated by qwe.\n")
    except FilesystemError as e:
        print(f"Warning: Could not create README.md: {e}", file=sys.stderr)

    # Add .gitignore
    gitignore_content = DEFAULT_GITIGNORE
    template_path = None
    if lang:
        template_path = paths["templates"] / f"{lang}.gitignore"
        if template_path.is_file():
            try:
                gitignore_content = safe_read_file(template_path)
                print(f"Using '{lang}' gitignore template.")
            except FilesystemError as e:
                print(f"Warning: Could not read template {template_path}: {e}. Using default.", file=sys.stderr)
        else:
            print(f"Warning: Language template '{lang}.gitignore' not found. Using default.", file=sys.stderr)
    else:
        print("Using default gitignore template.")

    try:
        safe_write_file(project_path / ".gitignore", gitignore_content)
    except FilesystemError as e:
        print(f"Warning: Could not create .gitignore: {e}", file=sys.stderr)

    # Initialize Git
    try:
        # Check if git command exists first
        # Use shutil.which for a more reliable check across platforms
        if not shutil.which("git"):
             raise FileNotFoundError("git command not found")

        # Initialize with main branch if possible
        result = subprocess.run(
            ["git", "init", "-b", "main"],
            cwd=project_path, capture_output=True, text=True, check=False, encoding='utf-8', errors='ignore'
        )
        if result.returncode != 0:
             # Fallback for older git versions
             subprocess.run(["git", "init"], cwd=project_path, capture_output=True, text=True, check=True, encoding='utf-8', errors='ignore')
             # Try to create and checkout main branch
             subprocess.run(["git", "checkout", "-b", "main"], cwd=project_path, capture_output=True, text=True, check=False, encoding='utf-8', errors='ignore')

        print(f"Initialized Git repository in {project_name}.")

        # Add initial commit (best effort)
        subprocess.run(["git", "add", "."], cwd=project_path, capture_output=True, check=False)
        # Check if there are changes to commit before committing
        status_result = subprocess.run(["git", "status", "--porcelain"], cwd=project_path, capture_output=True, text=True, check=False, encoding='utf-8', errors='ignore')
        if status_result.stdout:
            subprocess.run(["git", "commit", "-m", "Initial commit by qwe"], cwd=project_path, capture_output=True, text=True, check=False, encoding='utf-8', errors='ignore')
        else:
            print("No changes to commit initially.")


    except FileNotFoundError:
        print("Warning: 'git' command not found. Skipping Git initialization.", file=sys.stderr)
    except subprocess.CalledProcessError as e:
        # This catches errors from check=True calls if git exists but fails
        stderr_output = e.stderr.strip() if e.stderr else ""
        print(f"Warning: Git initialization failed: {stderr_output or e}", file=sys.stderr)
    except Exception as e:
         print(f"Warning: An unexpected error occurred during Git initialization: {e}", file=sys.stderr)


# --- Command Handler Functions ---

def handle_repo(args: argparse.Namespace) -> None:
    """Handles the 'qwe repo' command."""
    repo_name_arg = args.name
    target_dir = Path(".")

    if repo_name_arg == ".":
        repo_name = target_dir.resolve().name
        print(f"Using current directory name: '{repo_name}'")
        repo_root = target_dir.resolve()
    else:
        if not validate_name(repo_name_arg):
            exit_with_error("Invalid repository name provided.")
        repo_name = repo_name_arg
        target_dir = Path(repo_name)
        if target_dir.exists():
            exit_with_error(f"Directory or file '{target_dir}' already exists.")
        try:
            safe_makedirs(target_dir)
            print(f"Repository directory created: {target_dir}")
            repo_root = target_dir.resolve()
        except FilesystemError as e:
            exit_with_error(f"Failed to create directory {target_dir}: {e}")


    # Check if already inside another repo's managed area (but allow creation)
    parent_repo_root = find_repo_root(repo_root.parent)
    if parent_repo_root and parent_repo_root != repo_root: # Check it's not the root we just found/created
         print(f"Warning: Creating a repository inside another qwe-managed directory ({parent_repo_root}).", file=sys.stderr)

    # Check if the target directory itself is already a repo
    if (repo_root / QWE_DIR_NAME).exists():
        # Clean up if we created the dir
        if repo_root.name == repo_name and repo_name_arg != ".":
             try:
                 safe_rmtree(repo_root)
             except FilesystemError as cleanup_e:
                  print(f"Warning: Failed to cleanup partially created directory {repo_root}: {cleanup_e}", file=sys.stderr)
        exit_with_error(f"Directory '{repo_root}' already appears to be a qwe repository.")


    _init_repo_structure(repo_root, repo_name)
    print(f"Successfully initialized qwe repository '{repo_name}' at {repo_root}")

def handle_new(args: argparse.Namespace) -> None:
    """Handles the 'qwe new' command."""
    repo_root = find_repo_root()
    if not repo_root:
        raise RepoNotFoundError("Not inside a qwe repository. Use 'qwe repo <name>' to initialize.")

    project_name = args.project_name
    if not validate_name(project_name):
        exit_with_error("Invalid project name provided.")

    try:
        index_data = load_index(repo_root)
    except QweError as e:
        exit_with_error(f"Failed to load repository index: {e}")


    # Check for name conflicts (active, archived, or deleted)
    if get_project(index_data, project_name):
        raise QweError(f"Project named '{project_name}' already exists in the index.")

    # Use project_name directly as relative path for simplicity now
    project_rel_path = project_name
    project_path = repo_root / project_rel_path

    if project_path.exists():
        raise FilesystemError(f"Directory or file '{project_path.name}' already exists in {repo_root}.")

    try:
        safe_makedirs(project_path)
    except FilesystemError as e:
        raise FilesystemError(f"Failed to create project directory {project_path}: {e}")

    _create_project_files(repo_root, project_path, project_name, args.lang)

    # Update index.json
    new_project: ProjectDict = {
        "name": project_name,
        "created_at": get_timestamp(),
        "lang": args.lang if args.lang else "default",
        "state": "active",
        "path": project_rel_path # Store relative path
    }
    index_data["projects"].append(new_project)
    save_index(repo_root, index_data) # save_index handles errors

    print(f"Successfully created project '{project_name}'.")

def handle_show(args: argparse.Namespace) -> None:
    """Handles the 'qwe show' command."""
    repo_root = find_repo_root()
    if not repo_root:
        raise RepoNotFoundError("Not inside a qwe repository.")

    index_data = load_index(repo_root) # Handles errors
    projects = index_data.get("projects", [])
    paths = _get_qwe_paths(repo_root) # Get standard paths

    if args.project_name:
        # Show details for a specific project
        project = get_project(index_data, args.project_name)
        if not project:
            raise ProjectNotFoundError(f"Project '{args.project_name}' not found.")

        print(f"--- Project Details: {project['name']} ---")
        print(f"  Name:         {project['name']}")
        print(f"  Created At:   {project.get('created_at', 'N/A')}")
        print(f"  Language:     {project.get('lang', 'N/A')}")
        print(f"  State:        {project.get('state', 'unknown')}")

        state = project.get('state')
        rel_path = project.get('path')
        abs_path: Optional[Path] = None
        location_desc = "unknown"

        if state == 'active' and rel_path:
            abs_path = repo_root / rel_path
            location_desc = f"./{rel_path}"
            print(f"  Relative Path:{rel_path}")
        elif state == 'deleted' and rel_path:
            abs_path = repo_root / rel_path # Path should point to trash location
            location_desc = f"./{rel_path}"
            print(f"  Trash Location: ./{rel_path}")
            if project.get('original_path'):
                 print(f"  Original Path:./{project['original_path']}")
        elif state == 'archived':
            archive_rel_path = project.get("archive_path")
            if archive_rel_path:
                 location_desc = f"./{archive_rel_path}"
                 abs_path = repo_root / archive_rel_path # Path to the archive file
                 print(f"  Archive File: ./{archive_rel_path}")
            else:
                 # Estimate path if not found in index
                 location_desc = f"./{paths['archives'].name}/{project['name']}_*.tar.gz"
                 print(f"  Archive File: {location_desc} (estimated)")
            # Show original location before archive/delete
            original_loc = project.get('original_path') or project.get('path')
            if original_loc:
                 print(f"  Original/Kept Path: ./{original_loc}")


        # Check existence and Git info based on determined absolute path
        if abs_path:
            if state in ['active', 'deleted'] and abs_path.is_dir():
                 print(f"  Current Path: {abs_path}")
                 git_info = get_git_info(abs_path)
                 print(f"  Git Branch:   {git_info['branch']}")
                 print(f"  Last Commit:  {git_info['last_commit']}")
            elif state == 'archived' and abs_path.is_file():
                 print(f"  Archive Path: {abs_path}")
                 # Git info not applicable to the archive file itself
            elif not abs_path.exists():
                 print(f"  Current Path: '{abs_path}' (NOT FOUND!)")
            else:
                 print(f"  Current Path: '{abs_path}' (Unexpected type, not dir/file as expected for state)")
        else:
             # Handle cases where abs_path couldn't be determined but state is known
             if state == 'archived' and not project.get("archive_path"):
                 # Already printed estimated path
                 pass
             elif state in ['active', 'deleted'] and rel_path:
                  # If rel_path was set but abs_path is None (shouldn't happen often)
                  print(f"  Current Path: ./{rel_path} (NOT FOUND!)")
             else:
                 print(f"  Current Path: Not found or inaccessible")

    else:
        # List projects based on filter
        filter_state = "active" # Default
        if args.archived:
            filter_state = "archived"
        elif args.deleted:
            filter_state = "deleted"

        filtered_projects = [p for p in projects if isinstance(p, dict) and p.get("state") == filter_state]

        if not filtered_projects:
            print(f"No {filter_state} projects found.")
            return

        state_title = filter_state.capitalize()
        print(f"\n--- {state_title} Projects ---")
        print(f"{'Index':<6} {'Name':<25} {'Language':<10} {'Created At':<22} {'State':<10}")
        print("-" * 80)
        for i, project in enumerate(filtered_projects):
            # Only show index for active projects (for cd command)
            index_str = f"{i+1:<6}" if filter_state == "active" else f"{' ':<6}"
            created_at = project.get('created_at', 'N/A')
            # Truncate timezone for display if present
            if isinstance(created_at, str) and created_at.endswith('Z'):
                created_at = created_at[:-1]

            print(f"{index_str} {project.get('name', '?'):<25} {project.get('lang', 'N/A'):<10} {created_at:<22} {project.get('state', '?'):<10}")

def handle_archive(args: argparse.Namespace) -> None:
    """Handles the 'qwe archive' command."""
    repo_root = find_repo_root()
    if not repo_root:
        raise RepoNotFoundError("Not inside a qwe repository.")

    index_data = load_index(repo_root)
    project = get_project(index_data, args.project_name)
    paths = _get_qwe_paths(repo_root)

    if not project:
        raise ProjectNotFoundError(f"Project '{args.project_name}' not found.")

    if project.get("state") != "active":
        raise InvalidStateError(f"Project '{args.project_name}' is not active (state: {project.get('state')}). Cannot archive.")

    project_rel_path = project.get("path")
    if not project_rel_path:
         raise QweError(f"Active project '{args.project_name}' has no path defined in the index.")

    project_path = repo_root / project_rel_path
    if not project_path.is_dir():
         raise FilesystemError(f"Project directory not found at {project_path}. Check index integrity ('qwe doctor').")

    # Create archive name
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    # Sanitize project name slightly for filename safety (replace non-alphanumeric)
    safe_project_name = re.sub(r'[^\w\-.]', '_', project['name'])
    archive_basename = f"{safe_project_name}_{timestamp}.tar.gz"
    archive_path = paths["archives"] / archive_basename
    archive_rel_path = archive_path.relative_to(repo_root)

    print(f"Archiving '{project['name']}' to {archive_path}...")
    try:
        safe_makedirs(paths["archives"]) # Ensure archives dir exists
        with tarfile.open(archive_path, "w:gz") as tar:
            # Add the project directory to the archive.
            # arcname=project['name'] ensures it extracts with the original name
            tar.add(project_path, arcname=project['name'])
        print("Archive created successfully.")

        # Update index
        project["state"] = "archived"
        project["archive_path"] = str(archive_rel_path) # Store relative path to archive

        if not args.keep:
            print(f"Removing original project directory: {project_path}")
            project["original_path"] = project["path"] # Store original path before removing
            project["path"] = None # Mark path as None since it's removed
            safe_rmtree(project_path)
            print("Original directory removed.")
        else:
            # If keeping, the 'path' field remains unchanged
            project.pop("original_path", None) # Ensure no stale original_path
            print("Keeping original project directory (--keep specified).")

        save_index(repo_root, index_data)
        print(f"Index updated: '{project['name']}' marked as archived.")

    except tarfile.TarError as e:
        print(f"Error creating archive: {e}", file=sys.stderr)
        # Clean up potentially incomplete archive
        if archive_path.exists(): archive_path.unlink(missing_ok=True)
        exit_with_error("Archiving failed.", 1)
    except FilesystemError as e:
         print(f"Filesystem error during archiving: {e}", file=sys.stderr)
         if archive_path.exists(): archive_path.unlink(missing_ok=True)
         exit_with_error("Archiving failed.", 1)
    except Exception as e:
        print(f"An unexpected error occurred during archiving: {e}", file=sys.stderr)
        if archive_path.exists(): archive_path.unlink(missing_ok=True)
        exit_with_error("Archiving failed.", 1)

def handle_remove(args: argparse.Namespace) -> None:
    """Handles the 'qwe remove' command (soft delete)."""
    repo_root = find_repo_root()
    if not repo_root:
        raise RepoNotFoundError("Not inside a qwe repository.")

    index_data = load_index(repo_root)
    project = get_project(index_data, args.project_name)
    paths = _get_qwe_paths(repo_root)

    if not project:
        raise ProjectNotFoundError(f"Project '{args.project_name}' not found.")

    if project.get("state") != "active":
        raise InvalidStateError(f"Project '{args.project_name}' is not active (state: {project.get('state')}). Cannot remove.")

    project_rel_path = project.get("path")
    if not project_rel_path:
        raise QweError(f"Active project '{args.project_name}' has no path defined in the index.")

    project_path = repo_root / project_rel_path
    if not project_path.is_dir():
        raise FilesystemError(f"Project directory not found at {project_path}. Check index integrity ('qwe doctor').")

    # Determine unique name in trash
    trash_path_base = paths["trash"] / project['name']
    trash_path = trash_path_base
    counter = 1
    # Loop to find a unique name if the base name already exists
    while trash_path.exists():
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        trash_path = paths["trash"] / f"{project['name']}_{timestamp}_{counter}"
        counter += 1

    if trash_path != trash_path_base:
         print(f"Warning: '{project['name']}' already exists in trash. Moving to '{trash_path.name}'", file=sys.stderr)

    print(f"Moving '{project['name']}' to trash ({paths['trash'].name})...")
    try:
        safe_makedirs(paths["trash"]) # Ensure trash dir exists
        safe_move(project_path, trash_path)

        # Update index
        project["state"] = "deleted"
        project["original_path"] = project["path"] # Store original relative path
        project["path"] = str(trash_path.relative_to(repo_root)) # Update path to trash location (relative)
        project.pop("archive_path", None) # Clear any archive path info

        save_index(repo_root, index_data)
        print(f"Project '{args.project_name}' moved to trash and marked as deleted.")

    except FilesystemError as e:
        # Don't update index if move failed
        exit_with_error(f"Error moving project to trash: {e}")


def handle_recover(args: argparse.Namespace) -> None:
    """Handles the 'qwe recover' command."""
    repo_root = find_repo_root()
    if not repo_root:
        raise RepoNotFoundError("Not inside a qwe repository.")

    index_data = load_index(repo_root)
    paths = _get_qwe_paths(repo_root)

    try:
        if args.archived:
            project_name = args.archived
            recover_from_archive(repo_root, index_data, project_name, paths)
        elif args.removed:
            project_name = args.removed
            recover_from_trash(repo_root, index_data, project_name, paths)
        else:
             # Argparse mutually exclusive group should prevent this
             exit_with_error("Specify either --archived or --removed.")
    except QweError as e: # Catch specific errors from recovery functions
        exit_with_error(str(e))


def recover_from_archive(repo_root: Path, index_data: IndexDict, project_name: str, paths: Dict[str, Path]) -> None:
    """Recovers a project from the archives directory."""
    project = get_project(index_data, project_name)

    if not project or project.get("state") != "archived":
        raise InvalidStateError(f"Project '{project_name}' not found or is not archived.")

    archive_dir = paths["archives"]
    if not archive_dir.is_dir():
         raise FilesystemError(f"Archives directory '{archive_dir}' does not exist.")

    # Find the latest archive for this project
    # Use glob for pattern matching and check if file exists
    archive_pattern = f"{project_name}_*.tar.gz"
    archive_files = sorted(
        [f for f in archive_dir.glob(archive_pattern) if f.is_file()],
        key=lambda f: f.stat().st_mtime, # Sort by modification time
        reverse=True
    )


    if not archive_files:
        raise FilesystemError(f"No archive files found matching '{archive_pattern}' in {archive_dir.name}.")

    latest_archive = archive_files[0]
    print(f"Latest archive found: {latest_archive}")

    # Determine recovery path (use original path if stored, else project name)
    # Path might be None if archived without --keep, original_path should exist then
    recovery_path_relative_str = project.get("original_path") or project.get("path") or project_name
    recovery_path = repo_root / recovery_path_relative_str

    if recovery_path.exists():
        raise FilesystemError(f"Target path '{recovery_path}' already exists. Cannot recover.")

    print(f"Restoring '{project_name}' from {latest_archive.name} to '{recovery_path_relative_str}'...")
    try:
        # We extract *into* the parent directory of the recovery path.
        extract_target_dir = recovery_path.parent
        safe_makedirs(extract_target_dir) # Ensure parent exists

        with tarfile.open(latest_archive, "r:gz") as tar:
            # Basic security check: Ensure members don't extract outside target dir
            # Python 3.12+ has data filter, older versions need manual checks
            # For simplicity here, assume archives are trusted (created by qwe)
            # A more robust solution would inspect members before extraction
            tar.extractall(path=extract_target_dir)

        # Verify the expected directory was created after extraction
        # Assumes the tarball contains a single top-level directory named project_name
        # which might not match recovery_path.name if original_path was different
        extracted_path = extract_target_dir / project_name
        if not extracted_path.is_dir():
             # Maybe the tarball didn't have the top-level dir? Check recovery_path itself
             if not recovery_path.is_dir():
                 raise FilesystemError(f"Expected directory '{project_name}' or '{recovery_path.name}' not found after extraction in {extract_target_dir}.")

        # If the extracted dir is not the final recovery path, move it
        if extracted_path.is_dir() and extracted_path.resolve() != recovery_path.resolve():
             print(f"Moving extracted content from '{extracted_path.name}' to '{recovery_path.name}'...")
             safe_move(extracted_path, recovery_path)
        elif not recovery_path.is_dir(): # Double check final path exists
             raise FilesystemError(f"Final recovery path '{recovery_path}' not found after extraction and potential move.")


        print("Restore successful.")

        # Update index
        project["state"] = "active"
        project["path"] = recovery_path_relative_str # Restore relative path
        project.pop("archive_path", None)
        project.pop("original_path", None)
        save_index(repo_root, index_data)
        print(f"Index updated: '{project_name}' marked as active.")

    except tarfile.TarError as e:
        print(f"Error extracting archive: {e}", file=sys.stderr)
        # Attempt cleanup of potentially partially extracted files
        if recovery_path.exists(): safe_rmtree(recovery_path)
        raise QweError("Archive extraction failed.")
    except FilesystemError as e:
         print(f"Filesystem error during recovery: {e}", file=sys.stderr)
         if recovery_path.exists(): safe_rmtree(recovery_path)
         raise QweError("Recovery failed due to filesystem error.")
    except Exception as e:
        print(f"An unexpected error occurred during recovery: {e}", file=sys.stderr)
        if recovery_path.exists(): safe_rmtree(recovery_path)
        raise QweError("Recovery failed due to unexpected error.")

def recover_from_trash(repo_root: Path, index_data: IndexDict, project_name: str, paths: Dict[str, Path]) -> None:
    """Recovers a project from the trash directory."""
    project = get_project(index_data, project_name)

    if not project or project.get("state") != "deleted":
        raise InvalidStateError(f"Project '{project_name}' not found or is not deleted.")

    trash_dir = paths["trash"]
    if not trash_dir.is_dir():
         raise FilesystemError(f"Trash directory '{trash_dir}' does not exist.")

    # Find the project in the trash directory
    current_path_in_trash_rel = project.get("path")
    current_path_in_trash: Optional[Path] = None
    found_in_trash = False

    # 1. Check the path stored in the index first
    if current_path_in_trash_rel:
         potential_path = (repo_root / current_path_in_trash_rel).resolve()
         # Check if it exists and is actually inside the resolved trash directory
         try:
             if potential_path.is_dir() and potential_path.is_relative_to(trash_dir.resolve()):
                 current_path_in_trash = potential_path
                 found_in_trash = True
         except (ValueError, FileNotFoundError): # is_relative_to errors or trash missing
              pass # Path is invalid or outside trash

    # 2. If not found via index path, search the trash directory
    if not found_in_trash:
        print(f"Warning: Project not found at index path '{current_path_in_trash_rel}'. Searching trash...", file=sys.stderr)
        possible_matches = [
            item for item in trash_dir.iterdir()
            if item.is_dir() and (item.name == project_name or item.name.startswith(project_name + "_"))
        ]
        if len(possible_matches) == 1:
             current_path_in_trash = possible_matches[0].resolve() # Use resolved path
             current_path_in_trash_rel = str(current_path_in_trash.relative_to(repo_root))
             print(f"Found at: {current_path_in_trash}", file=sys.stderr)
             found_in_trash = True
             # Optionally update index path here? For now, just use the found path.
        elif len(possible_matches) > 1:
             raise QweError(f"Multiple matching directories found in trash for '{project_name}'. Cannot determine which to recover.")
        # else: still not found

    if not found_in_trash or not current_path_in_trash:
         raise FilesystemError(f"Project directory '{project_name}' not found in trash.")

    # Determine recovery path (use original path stored during removal)
    recovery_path_relative_str = project.get("original_path")
    if not recovery_path_relative_str:
         # Fallback to project name in root - provide warning
         recovery_path_relative_str = project_name
         print(f"Warning: Original path not found in index. Recovering to default location: '{recovery_path_relative_str}'", file=sys.stderr)

    recovery_path = repo_root / recovery_path_relative_str

    if recovery_path.exists():
        raise FilesystemError(f"Target path '{recovery_path}' already exists. Cannot recover.")

    print(f"Moving '{current_path_in_trash.name}' from trash back to '{recovery_path_relative_str}'...")
    try:
        # Ensure parent directory exists before moving
        safe_makedirs(recovery_path.parent)
        safe_move(current_path_in_trash, recovery_path)

        # Update index
        project["state"] = "active"
        project["path"] = recovery_path_relative_str # Restore original relative path
        project.pop("original_path", None)
        project.pop("archive_path", None) # Clean up just in case
        save_index(repo_root, index_data)

        print(f"Project '{project_name}' recovered successfully and marked as active.")

    except FilesystemError as e:
        # Don't update index if move failed
        raise QweError(f"Error moving project from trash: {e}")


def handle_purge(args: argparse.Namespace) -> None:
    """Handles the 'qwe purge' command."""
    repo_root = find_repo_root()
    if not repo_root:
        raise RepoNotFoundError("Not inside a qwe repository.")

    index_data = load_index(repo_root)
    paths = _get_qwe_paths(repo_root)
    # Ensure projects list exists and filter deleted ones
    deleted_projects = [p for p in index_data.get("projects", []) if isinstance(p, dict) and p.get("state") == "deleted"]
    trash_dir = paths["trash"]

    if not deleted_projects:
        print("No deleted projects found in the trash to purge.")
        return

    print("--- Deleted Projects in Trash ---")
    projects_to_purge_info: List[Tuple[ProjectDict, Optional[Path]]] = [] # Store project dict and its path in trash if found
    for i, project in enumerate(deleted_projects):
        name = project.get('name', 'UNKNOWN')
        path_in_index = project.get("path")
        display_path = "unknown"
        abs_trash_path: Optional[Path] = None

        # Try to locate the actual item in trash
        if path_in_index:
             potential_path = (repo_root / path_in_index).resolve()
             try:
                  if potential_path.exists() and potential_path.is_relative_to(trash_dir.resolve()):
                       abs_trash_path = potential_path
                       display_path = f"./{path_in_index}"
                  else:
                       display_path = f"./{path_in_index} (not found or outside trash!)"
             except (ValueError, FileNotFoundError):
                  display_path = f"./{path_in_index} (invalid path or trash missing!)"
        else:
             display_path = "(path missing in index)"

        # If not found via index, try searching trash (more reliable for purge)
        if not abs_trash_path and trash_dir.is_dir():
             possible_matches = [
                 item for item in trash_dir.iterdir()
                 if item.exists() and (item.name == name or item.name.startswith(name + "_"))
             ]
             if len(possible_matches) == 1:
                  abs_trash_path = possible_matches[0].resolve()
                  display_path += f" (found at ./{TRASH_DIR_NAME}/{abs_trash_path.name})"
             elif len(possible_matches) > 1:
                  display_path += f" (multiple matches found in trash!)"


        print(f"  {i+1}. {name} (Location: {display_path})")
        projects_to_purge_info.append((project, abs_trash_path))


    print("\nThis action will permanently remove the corresponding directories from the .trash folder")
    print("and delete their entries from the index file.")
    if not get_user_confirmation("Are you sure you want to proceed?"):
        print("Purge cancelled.")
        return

    print("Purging deleted projects...")
    purged_index_count = 0
    deleted_dir_count = 0
    failed_dir_count = 0
    projects_to_keep: List[ProjectDict] = [p for p in index_data.get("projects", []) if not (isinstance(p, dict) and p.get("state") == "deleted")]
    filesystem_errors: List[str] = []


    for project, path_to_delete in projects_to_purge_info:
        project_name = project.get('name', 'UNKNOWN')
        purged_index_count += 1 # Always remove index entry if confirmed

        if path_to_delete:
            if path_to_delete.exists():
                print(f"Removing directory: {path_to_delete}")
                try:
                    safe_rmtree(path_to_delete)
                    deleted_dir_count += 1
                except FilesystemError as e:
                    filesystem_errors.append(f"  - {project_name}: {e}")
                    failed_dir_count += 1
                    projects_to_keep.append(project) # Keep index entry if dir removal failed
            else:
                 # Path was identified but doesn't exist anymore (maybe deleted manually?)
                 print(f"Warning: Directory for '{project_name}' at {path_to_delete} not found. Removing index entry only.", file=sys.stderr)
        else:
             # Directory couldn't be reliably identified in trash
             print(f"Warning: Directory for deleted project '{project_name}' could not be reliably located in trash. Removing index entry only.", file=sys.stderr)


    # Update the index file
    index_data["projects"] = projects_to_keep
    save_index(repo_root, index_data)

    print(f"\nPurge complete.")
    print(f"Index entries removed: {purged_index_count}.")
    print(f"Directories successfully deleted: {deleted_dir_count}.")
    if failed_dir_count > 0:
        print(f"Failed to delete {failed_dir_count} director(y/ies):")
        for error in filesystem_errors:
            print(error, file=sys.stderr)
        print("Check errors above. Index entries for failed directory deletions were kept.")


def handle_doctor(args: argparse.Namespace) -> None:
    """Handles the 'qwe doctor' command."""
    repo_root = find_repo_root()
    if not repo_root:
        raise RepoNotFoundError("Not inside a qwe repository.")

    print(f"--- Running QWE Doctor on Repository: {repo_root.name} ---")
    print(f"Repository Root: {repo_root}")

    issues_found = 0
    warnings_found = 0
    paths = _get_qwe_paths(repo_root)

    # Check core structure
    print("\nChecking core structure...")
    core_items = {
        QWE_DIR_NAME: paths["qwe_dir"],
        INDEX_FILE_NAME: paths["index"],
        TEMPLATES_DIR_NAME: paths["templates"],
        TRASH_DIR_NAME: paths["trash"],
        ARCHIVES_DIR_NAME: paths["archives"],
    }
    for name, path in core_items.items():
        is_dir = not name.endswith('.json')
        exists = path.is_dir() if is_dir else path.is_file()
        status = "[OK]" if exists else "[MISSING]"
        if not exists:
             issues_found += 1
             status = f"[MISSING] - Run 'qwe repo .' to attempt recreation?" if name == INDEX_FILE_NAME else "[MISSING]"

        print(f"  - {name:<20} {'Directory' if is_dir else 'File'} {' ' * 5} Status: {status}")

    if not paths["index"].is_file():
        exit_with_error("\nCritical Error: index.json is missing. Cannot check projects.", 1)

    # Load index and check projects
    print("\nChecking projects listed in index.json...")
    try:
        index_data = load_index(repo_root)
        projects = index_data.get("projects", [])
        if not projects:
            print("  - No projects found in index.")

        project_names = set()
        for i, project in enumerate(projects):
             # Basic check if project entry is a dictionary
             if not isinstance(project, dict):
                  print(f"\n  Project Entry {i+1}: [INVALID] - Not a valid dictionary object in index.")
                  issues_found += 1
                  continue

             name = project.get("name")
             state = project.get("state")
             rel_path_str = project.get("path") # Path relative to repo root (or None)
             archive_rel_path_str = project.get("archive_path") # Path relative to repo root
             original_rel_path_str = project.get("original_path") # Path relative to repo root

             print(f"\n  Project {i+1}: '{name or 'MISSING NAME!'}' (State: {state or 'UNKNOWN'})")

             # --- Basic Validation ---
             valid_entry = True
             if not name:
                  print(f"    [Issue] Project name is missing.")
                  issues_found += 1
                  valid_entry = False
             elif name in project_names:
                  print(f"    [Issue] Duplicate project name found: '{name}'.")
                  issues_found += 1
                  # Don't mark valid_entry=False, but it's an issue
             else:
                  project_names.add(name)

             if state not in ["active", "archived", "deleted"]:
                  print(f"    [Issue] Unknown or missing state: '{state}'.")
                  issues_found += 1
                  valid_entry = False

             if not valid_entry:
                  continue # Skip location checks if basic info is wrong

             # --- Location Checks based on State ---
             if state == "active":
                 if not rel_path_str:
                      print(f"    [Issue] Active project is missing 'path'.")
                      issues_found += 1
                 else:
                     abs_path = repo_root / rel_path_str
                     if not abs_path.is_dir():
                          print(f"    [Issue] Active project directory not found at: ./{rel_path_str}")
                          issues_found += 1
                     else:
                          print(f"    [OK] Active directory found at: ./{rel_path_str}")
                          # Git check (warning only)
                          if not (abs_path / ".git").is_dir():
                               print(f"    [Warning] Active project at './{rel_path_str}' is missing .git directory.")
                               warnings_found += 1

             elif state == "deleted":
                  if not rel_path_str:
                       print(f"    [Issue] Deleted project is missing 'path' (should point to trash location).")
                       issues_found += 1
                  else:
                       abs_path = repo_root / rel_path_str
                       if not abs_path.is_dir():
                            print(f"    [Issue] Deleted project directory not found at: ./{rel_path_str}")
                            issues_found += 1
                       else:
                            # Check if it's actually in the trash directory
                            try:
                                 if not abs_path.resolve().is_relative_to(paths["trash"].resolve()):
                                      print(f"    [Issue] Path './{rel_path_str}' for deleted project is not inside {paths['trash'].name}.")
                                      issues_found += 1
                                 else:
                                      print(f"    [OK] Deleted directory found in trash at: ./{rel_path_str}")
                            except (ValueError, FileNotFoundError): # is_relative_to errors or trash missing
                                 print(f"    [Issue] Could not verify if './{rel_path_str}' is inside the trash directory.")
                                 issues_found += 1
                  if not original_rel_path_str:
                       print(f"    [Warning] Deleted project is missing 'original_path' for recovery.")
                       warnings_found += 1

             elif state == "archived":
                  archive_found = False
                  if archive_rel_path_str:
                       abs_archive_path = repo_root / archive_rel_path_str
                       if not abs_archive_path.is_file():
                            print(f"    [Issue] Archived project archive file not found at: ./{archive_rel_path_str}")
                            issues_found += 1
                       else:
                            print(f"    [OK] Archive file found at: ./{archive_rel_path_str}")
                            archive_found = True
                  else:
                       # Check if *any* archive exists if specific path missing
                       expected_location_desc = f"./{paths['archives'].name}/{name}_*.tar.gz"
                       try:
                            if paths["archives"].is_dir():
                                 matching_archives = list(paths["archives"].glob(f"{name}_*.tar.gz"))
                                 if not matching_archives:
                                      print(f"    [Issue] No matching archive files found in {paths['archives'].name} for '{name}'.")
                                      issues_found += 1
                                 else:
                                      print(f"    [OK] Found one or more archive files in {paths['archives'].name} (e.g., {matching_archives[0].name})")
                                      archive_found = True
                            else:
                                 print(f"    [Issue] Archives directory '{paths['archives'].name}' does not exist.")
                                 issues_found += 1
                       except Exception as e:
                            print(f"    [Error] Checking archives: {e}")
                            issues_found += 1

                  # Check consistency of original path based on whether it was kept
                  if rel_path_str: # Path exists -> implies --keep was used
                       abs_kept_path = repo_root / rel_path_str
                       if not abs_kept_path.is_dir():
                            print(f"    [Warning] Original directory './{rel_path_str}' is missing (expected with --keep).")
                            warnings_found += 1
                       if original_rel_path_str:
                            print(f"    [Warning] 'original_path' should not be present when archived with --keep.")
                            warnings_found += 1
                  elif original_rel_path_str: # No path, but original_path -> implies --keep was NOT used
                       abs_original_path = repo_root / original_rel_path_str
                       if abs_original_path.exists(): # Should not exist if removed
                            print(f"    [Warning] Original path './{original_rel_path_str}' still exists (should have been removed).")
                            warnings_found += 1
                  elif not original_rel_path_str and not rel_path_str:
                       # This case indicates an issue, likely during archive without keep
                       print(f"    [Warning] Archived project has neither 'path' nor 'original_path'. Recovery might default to project name.")
                       warnings_found += 1


    except json.JSONDecodeError as e:
        print(f"  [Critical Error] index.json is corrupted: {e}", file=sys.stderr)
        issues_found += 1
    except QweError as e: # Catch errors from load_index etc.
         print(f"  [Error] {e}", file=sys.stderr)
         issues_found += 1
    except Exception as e:
        print(f"  [Error] An unexpected error occurred while checking projects: {e}", file=sys.stderr)
        issues_found += 1


    print("\n--- Doctor Check Complete ---")
    if issues_found == 0 and warnings_found == 0:
        print("No issues found. Your qwe repository looks healthy!")
    else:
        print(f"Found {issues_found} issue(s) and {warnings_found} warning(s).")
        print("Please review the messages above.")
        if issues_found > 0:
             sys.exit(1) # Exit with error code only if actual issues were found


def handle_number(number_str: str) -> None:
    """Handles the 'qwe <number>' command to output a project path."""
    repo_root = find_repo_root()
    if not repo_root:
        # Exit silently for use with cd $(qwe 1)
        sys.exit(1)

    try:
        index = int(number_str) - 1 # User enters 1-based index
        if index < 0: raise ValueError("Index must be 1 or greater")
    except ValueError:
        exit_with_error(f"'{number_str}' is not a valid project index number (must be 1 or greater).")


    try:
        index_data = load_index(repo_root)
        # Filter for active projects AND ensure they have a valid path
        active_projects = [
            p for p in index_data.get("projects", [])
            if isinstance(p, dict) and p.get("state") == "active" and p.get("path")
        ]

        if 0 <= index < len(active_projects):
            project = active_projects[index]
            # Assume project["path"] is valid string due to filter above
            project_path_abs = repo_root / project["path"] # type: ignore
            # Output only the absolute path, suitable for cd
            print(project_path_abs)
            sys.exit(0) # Success
        else:
            print(f"Error: No active project found with index {number_str}.", file=sys.stderr)
            print(f"There are {len(active_projects)} active project(s). Run 'qwe show' to list them.", file=sys.stderr)
            sys.exit(1)

    except QweError as e:
         exit_with_error(f"Error accessing project index: {e}")
    except Exception as e:
         exit_with_error(f"Unexpected error: {e}")


def handle_install(args: argparse.Namespace) -> None:
    """Handles the 'qwe install' command."""
    print("Attempting to install qwe...")

    # 1. Determine target directory
    home_dir = Path.home()
    possible_dirs = [
        home_dir / ".local" / "bin",
        home_dir / "bin"
    ]
    target_dir: Optional[Path] = None

    for directory in possible_dirs:
        if directory.is_dir():
            target_dir = directory
            print(f"Found existing target directory: {target_dir}")
            break
        elif not directory.exists():
            try:
                print(f"Attempting to create directory: {directory}")
                safe_makedirs(directory)
                target_dir = directory
                print(f"Successfully created directory: {target_dir}")
                break
            except FilesystemError as e:
                print(f"Warning: Could not create {directory}: {e}. Trying next option...", file=sys.stderr)

    if target_dir is None:
        exit_with_error(f"Could not find or create a standard installation directory ({possible_dirs[0]} or {possible_dirs[1]}).")


    # 2. Determine source script path
    try:
        # __file__ should be the path to this script when run normally
        source_path = Path(__file__).resolve()
    except NameError:
        # __file__ might not be defined if code is executed differently (e.g., exec)
        # Fallback: try using sys.argv[0] if it seems plausible
        if sys.argv[0] and Path(sys.argv[0]).is_file():
             source_path = Path(sys.argv[0]).resolve()
             print(f"Warning: __file__ not defined, using sys.argv[0] as source: {source_path}", file=sys.stderr)
        else:
             exit_with_error("Could not determine the path of the current script.")


    target_path = target_dir / SCRIPT_NAME

    # 3. Check if already installed and handle overwrite
    if target_path.exists():
         try:
             if target_path.samefile(source_path):
                  print(f"'{SCRIPT_NAME}' appears to be already installed and is this exact script ({target_path}).")
                  print("Skipping installation.")
                  # Check PATH and inform user anyway
                  _check_and_inform_path(target_dir)
                  sys.exit(0)
             else:
                  print(f"Warning: A file already exists at '{target_path}' which is not this script.")
                  if not get_user_confirmation("Do you want to overwrite it?"):
                       print("Installation cancelled.")
                       sys.exit(1)
                  else:
                       try:
                            target_path.unlink()
                            print(f"Removed existing file at {target_path}.")
                       except OSError as e:
                            exit_with_error(f"Could not remove existing file at {target_path}: {e}")

         except OSError as e: # Catches samefile error if target is broken link etc.
             print(f"Warning: Could not compare existing file at '{target_path}': {e}")
             if not get_user_confirmation("Do you want to try overwriting it?"):
                  print("Installation cancelled.")
                  sys.exit(1)
             else:
                  try:
                       target_path.unlink(missing_ok=True) # Attempt remove anyway
                       print(f"Attempted to remove existing file/link at {target_path}.")
                  except OSError as e_unlink:
                       exit_with_error(f"Could not remove existing file/link at {target_path}: {e_unlink}")


    # 4. Copy the script
    try:
        shutil.copy2(source_path, target_path) # copy2 preserves metadata (like permissions if possible)
        print(f"Script copied to: {target_path}")
    except Exception as e:
        exit_with_error(f"Error copying script to {target_path}: {e}")


    # 5. Ensure execute permissions
    try:
        # Set permissions: user=rwx, group=rx, other=rx (standard executable)
        target_path.chmod(0o755)
        print("Execute permissions set (rwxr-xr-x).")
    except OSError as e:
        # This might fail on some filesystems (e.g., FAT) or if user lacks permissions
        print(f"Warning: Could not set execute permissions on {target_path}: {e}", file=sys.stderr)
        print("Installation might still work depending on the filesystem mount options.", file=sys.stderr)


    # 6. Inform user about PATH
    _check_and_inform_path(target_dir)
    print("\nInstallation completed (manual PATH adjustment may be needed).")

def handle_uninstall(args: argparse.Namespace) -> None:
    """Handles the 'qwe uninstall' command."""
    print("Attempting to uninstall qwe...")

    installed_path = find_executable_path(SCRIPT_NAME)

    if not installed_path:
        exit_with_error(f"Executable '{SCRIPT_NAME}' not found in standard paths (~/.local/bin, ~/bin).\nIs it installed or in a different location?")


    print(f"Found executable at: {installed_path}")

    if not get_user_confirmation(f"Are you sure you want to delete '{installed_path}'?"):
        print("Uninstallation cancelled.")
        return

    try:
        installed_path.unlink()
        print(f"Successfully deleted '{installed_path}'.")
        print("\nIf you manually added the directory")
        print(f"'{installed_path.parent}' to your PATH,")
        print("you may want to remove it from your shell configuration file (.bashrc, .zshrc, etc.).")
    except OSError as e:
        exit_with_error(f"Could not delete '{installed_path}': {e}")
    except Exception as e:
        exit_with_error(f"Unexpected error during deletion: {e}")


def _check_and_inform_path(install_dir: Path) -> None:
    """Checks if the installation directory is in PATH and informs the user."""
    print("\n--- PATH Check ---")
    print(f"The script '{SCRIPT_NAME}' has been installed in: {install_dir}")
    print("\nTo use the 'qwe' command directly from anywhere,")
    print(f"the directory '{install_dir}'")
    print("needs to be included in your PATH environment variable.")

    current_path_env = os.environ.get("PATH", "")
    path_dirs = current_path_env.split(os.pathsep)
    # Check against resolved path for robustness
    install_dir_resolved_str = str(install_dir.resolve())

    # Check common ways the path might be included
    path_found = install_dir_resolved_str in path_dirs or str(install_dir) in path_dirs

    if path_found:
        print(f"\nGood news! It looks like '{install_dir}' is already in your PATH.")
        print("You should be able to run 'qwe --version' in a new terminal session.")
    else:
        print("\nIt looks like this directory is NOT currently in your PATH.")
        print("You can add it by editing your shell configuration file.")
        print("Open a new terminal session after making the change.")
        print("\nCommon examples (choose the one for your shell):")
        print("-" * 30)
        # Provide examples for common shells
        print("For Bash (common on Linux):")
        print(f'  echo \'export PATH="{install_dir_resolved_str}:$PATH"\' >> ~/.bashrc')
        print("  source ~/.bashrc")
        print("\nFor Zsh (common on modern macOS):")
        print(f'  echo \'export PATH="{install_dir_resolved_str}:$PATH"\' >> ~/.zshrc')
        print("  source ~/.zshrc")
        print("\nFor Fish:")
        # Fish >= 3.0 recommended way
        print(f'  fish_add_path "{install_dir_resolved_str}"')
        # Older fish: print(f'  set -U fish_user_paths "{install_dir_str}" $fish_user_paths')
        print("\nAlternatively, add to ~/.profile (may work for various shells, requires login/logout):")
        print(f'  echo \'export PATH="{install_dir_resolved_str}:$PATH"\' >> ~/.profile')
        print("-" * 30)

# --- Argument Parsing and Main Execution ---

def create_parser() -> argparse.ArgumentParser:
    """Creates the argument parser for the CLI."""
    parser = argparse.ArgumentParser(
        description="QWE: Minimalist local CLI tool for managing development projects.",
        epilog="Example: qwe new mywebapp --lang python",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter # Show defaults in help
    )
    parser.add_argument(
        '--version',
        action='version',
        version=f'%(prog)s {VERSION}'
    )

    # Use title for better help organization
    subparsers = parser.add_subparsers(dest="command", title="Available Commands", required=False) # Make command optional for number handling

    # --- Install/Uninstall Commands ---
    parser_install = subparsers.add_parser(
        "install",
        help="Install the qwe script to ~/.local/bin or ~/bin for global use."
    )
    parser_install.set_defaults(func=handle_install)

    parser_uninstall = subparsers.add_parser(
        "uninstall",
        help="Remove the installed qwe script from ~/.local/bin or ~/bin."
    )
    parser_uninstall.set_defaults(func=handle_uninstall)


    # --- Repository Commands ---
    parser_repo = subparsers.add_parser("repo", help="Create a new project repository.")
    parser_repo.add_argument("name", help="Name for the repository directory, or '.' to use the current directory name.")
    parser_repo.set_defaults(func=handle_repo)

    # --- Project Commands ---
    parser_new = subparsers.add_parser("new", help="Create a new project within the current repository.")
    parser_new.add_argument("project_name", help="Name for the new project directory.")
    parser_new.add_argument("--lang", help="Language for .gitignore template (e.g., python).")
    parser_new.set_defaults(func=handle_new)

    parser_show = subparsers.add_parser("show", help="List projects or show details for one project.")
    show_group = parser_show.add_mutually_exclusive_group()
    show_group.add_argument("project_name", nargs="?", help="Show details for a specific project.")
    show_group.add_argument("--archived", action="store_true", help="List only archived projects.")
    show_group.add_argument("--deleted", action="store_true", help="List only deleted (in trash) projects.")
    parser_show.set_defaults(func=handle_show)

    parser_archive = subparsers.add_parser("archive", help="Archive a project (compress to .archives).")
    parser_archive.add_argument("project_name", help="Name of the project to archive.")
    parser_archive.add_argument("--keep", action="store_true", help="Keep the original project directory after archiving.")
    parser_archive.set_defaults(func=handle_archive)

    parser_remove = subparsers.add_parser("remove", help="Move a project to the .trash directory (soft delete).")
    parser_remove.add_argument("project_name", help="Name of the project to remove.")
    parser_remove.set_defaults(func=handle_remove)

    parser_recover = subparsers.add_parser("recover", help="Restore a project from archives or trash.")
    recover_group = parser_recover.add_mutually_exclusive_group(required=True)
    recover_group.add_argument("--archived", metavar="PROJECT_NAME", help="Name of the archived project to restore.")
    recover_group.add_argument("--removed", metavar="PROJECT_NAME", help="Name of the deleted project to recover from trash.")
    parser_recover.set_defaults(func=handle_recover)

    parser_purge = subparsers.add_parser("purge", help="Permanently delete projects from the .trash directory.")
    parser_purge.set_defaults(func=handle_purge)

    # --- Maintenance Commands ---
    parser_doctor = subparsers.add_parser("doctor", help="Check repository integrity.")
    parser_doctor.set_defaults(func=handle_doctor)

    return parser

def main() -> None:
    """Main execution function."""
    parser = create_parser()

    # Handle the 'qwe <number>' case before full parsing
    # Check if exactly two arguments are given, and the second one is a positive integer
    if len(sys.argv) == 2 and sys.argv[1].isdigit() and sys.argv[1] != '0':
         # Check it's not a subcommand name that happens to be a number
         subparser_actions = [
             action for action in parser._actions
             if isinstance(action, argparse._SubParsersAction)]
         # Safely get choices, handle case where subparsers might not exist
         subcommand_names = list(subparser_actions[0].choices.keys()) if subparser_actions else []

         if sys.argv[1] not in subcommand_names:
             try:
                 # Directly call the handler for the number command
                 handle_number(sys.argv[1])
                 sys.exit(0) # Exit successfully after handling number command
             except Exception as e:
                 # Catch potential errors within handle_number itself
                 exit_with_error(f"Error processing project index: {e}")

         # else: it's a number but also a subcommand name, let normal parsing handle it

    # Normal argument parsing
    args = parser.parse_args()

    # Execute the corresponding function if a command was provided
    if hasattr(args, "func"):
        try:
            # Commands that don't require being inside a repo
            if args.command in ['install', 'uninstall', 'repo']:
                 args.func(args)
            else:
                 # For other commands, ensure we are inside a repo
                 if not find_repo_root():
                      # Raise specific error for clarity
                      raise RepoNotFoundError("Not inside a qwe repository or command is not 'repo', 'install', or 'uninstall'.\nUse 'qwe repo <name>' to initialize a repository.")
                 args.func(args)

        except QweError as e:
            # Handle specific qwe errors gracefully
            exit_with_error(str(e))
        except KeyboardInterrupt:
             print("\nOperation cancelled by user.", file=sys.stderr)
             sys.exit(130) # Standard exit code for Ctrl+C
        except Exception as e:
             # Catch any other unexpected errors
             print(f"\nUnexpected Error: {type(e).__name__}: {e}", file=sys.stderr)
             # Optional: Add traceback for debugging in development
             # import traceback
             # traceback.print_exc()
             sys.exit(2) # General error exit code
    else:
        # No command was specified (e.g., just 'qwe' or 'qwe --version')
        # If --version was used, argparse handles it and exits.
        # If just 'qwe', print help.
        if len(sys.argv) == 1:
             parser.print_help()
        # Argparse handles other cases like invalid commands


if __name__ == "__main__":
    main()
