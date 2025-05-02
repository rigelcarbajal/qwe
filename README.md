# qwe

A minimalist local CLI tool for managing development projects. Organize, archive, and recover your projects directly from the command line.

## Features

- **Repository Management:** Initialize a new repository to manage your projects.
- **Project Creation:** Quickly create new project directories with basic structure and `.gitignore`.
- **Project Listing & Details:** View your projects and get detailed information about their state.
- **Archiving:** Compress and move projects to an archive directory, with an option to keep the original.
- **Soft Deteletion:** Move projects to a trash directory for temporary removal.
- **Recovery:** Restore projects from the archive or trash.
- **Purging:** Permanently delete projects from the trash.
- **Doctor:** Check the integrity of your qwe repository.
- **Installation:** Easily install the script for global use.
- **Uninstallation:** Remove the installed script.

## Installation

1.  **Save the script:** Save the `qwe.py` script to your local machine.
2.  **Make it executable:**
    ```bash
    chmod +x qwe.py
    ```
3.  **Run the install command:**
    ```bash
    ./qwe.py install
    ```
    This will attempt to copy the script to a standard user binary directory (like `~/.local/bin` or `~/bin`) and set executable permissions.

4.  **Add installation directory to PATH (if needed):** The installer will check if the installation directory is in your system's PATH and provide instructions if it's not. You might need to add a line like `export PATH="$HOME/.local/bin:$PATH"` to your shell's configuration file (`~/.bashrc`, `~/.zshrc`, etc.) and restart your terminal.

## Usage

Navigate to the directory where you want to manage your projects.

### Initialize a Repository

```bash
qwe repo my_projects_repo
```

This creates a `my_projects_repo` directory with a `.qwe` subdirectory to store its index and configurations. You can also initialize the current directory:

```bash
qwe repo .
```

### Create a New Project
Navigate into your repository directory `(my_projects_repo)`.

```bash
cd my_projects_repo
qwe new my_awesome_project
```

This creates a `my_awesome_project` subdirectory, adds a `README.md`, a default `.gitignore`, and initializes a `Git` repository inside it.

You can specify a language for a more specific `.gitignore` template (if available in `.qwe/templates`):


```bash
qwe new my_python_tool --lang python
```

### List Projects

List all active projects:
```bash
qwe show
```

List archived projects:
```bash
qwe show --archived
```

List deleted projects `(in trash)`:
```bash
qwe show --deleted
```

### Show Project Details
View details for a specific project:
```bash
qwe show my_awesome_project
```

You can also use the index number shown in qwe show for active projects to get the path:
```bash
cd $(qwe 1)
```
(Where '1' is the index of the project you want to navigate to).

### Archive a Project
Archive an active project. This moves it to the `.archives` directory as a `.tar.gz` file by default.

```bash
qwe archive my_awesome_project
```

Keep the original directory after archiving:

```bash
qwe archive my_awesome_project --keep
```

### Remove a Project (Soft Delete)
Move an active project to the `.trash` directory.
```bash
qwe remove my_python_tool
```

### Recover a Project
Recover a project from `.trash`:

```bash
qwe recover --removed my_python_tool
```

Recover a project from `archives`:

```bash
qwe recover --archived my_awesome_project
```

### Purge Deleted Projects
Permanently delete all projects currently in the `.trash` directory. Use with caution, this action is irreversible.

```bash
qwe purge
```

### Run Doctor Check
Check the integrity of your qwe repository, verifying index entries and file locations.
```bash
qwe doctor
```

### Uninstall
Remove the installed qwe script.
```bash
qwe uninstall
```


### License
This project is licensed under the MIT License.

### Contributing
If you'd like to contribute, please feel free to fork the repository and submit pull requests.



<sub>Made with ♥️ by <a href="https://www.rigelcarbajal.com">Rigel Carbajal</a></sub>


---

#### Acknowledgments
<sub>
- ChatGPT (by OpenAI)<br>
- Gemini (by Google) 
<br>
- The open source community — For continuous inspiration.
</sub>
