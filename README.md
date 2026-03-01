# Git Rev News Tools

A collection of scripts for preparing and publishing the [Git Rev News](https://git.github.io/rev_news/rev_news/) newsletter.

## Scripts

### `get_releases.py` — Collect Git-related releases

Scrapes and fetches recent releases for Git-related tools and writes them to `releases.md` in Markdown format.

**Supported software:** Git, Git for Windows, libgit2, libgit2sharp, GitHub Enterprise, GitLab, Bitbucket Data Center, Gerrit Code Review, GitKraken, GitHub Desktop, Sourcetree, tig, Garden, Git Cola, GitButler, Sublime Merge, Kinetic Merge, git-credential-azure, git-credential-oauth.

**Dependencies:**

```sh
pip install bs4 lxml requests
```

**Usage:**

```sh
# List all supported software and their source URL
./get_releases.py --list

# Fetch releases from the last 30 days (requires GitHub credentials for GitHub repos)
./get_releases.py --user <github-user> --password <github-token>

# Fetch releases since a specific date
./get_releases.py --since 2024-01-01 --user <github-user> --password <github-token>

# Fetch releases for a specific tool only
./get_releases.py --get git --user <github-user> --password <github-token>
```

A GitHub Personal Access Token with the `public_repo` scope is required for GitHub-hosted projects. Tokens can be created at https://github.com/settings/tokens.

Output is written to `releases.md`.

---

### `search_mailing_list.py` — Browse and export Git mailing list threads

An interactive terminal UI (TUI) for browsing recent threads from the Git mailing list and exporting selected ones as plain text files, suitable for inclusion in the newsletter.

**Dependencies:** `lei` (from [public-inbox](https://git.kernel.org/pub/scm/public-inbox/public-inbox.git)), `bs4`, `requests`

**Usage:**

```sh
./search_mailing_list.py
```

On first run, it will offer to clone the Git mailing list mirror locally. On subsequent runs, it will offer to update it. Threads can be browsed, searched, and selected; selected threads are saved to a `threads_YYYY_MM_DD/` directory as text files.

---

### `git_ml_converter.py` — Convert mailing list threads to plain text

Converts Git mailing list threads (fetched via `lei`, a URL, or a local HTML file) to plain text, preserving thread structure and code diffs.

**Usage:**

```sh
# From a Message-ID (uses lei)
./git_ml_converter.py <message-id> [-o output.txt] [--repo /path/to/mirror]

# From a URL
./git_ml_converter.py https://lore.kernel.org/git/...

# From a local HTML file
./git_ml_converter.py archive.html -o output.txt
```

---

### `publish_edition.sh` — Publish an edition and create the next draft

To be run from the root of the [git.github.io](https://github.com/git/git.github.io) repository. Publishes the current draft edition (creates a commit), creates a new empty draft for the next edition (creates another commit), pushes both, and opens a GitHub issue for the upcoming edition.

**Dependencies:** `git`, `gh` (GitHub CLI), `perl`

**Usage:**

```sh
../getreleases/publish_edition.sh [--token <github-pat>] [<next date> [<cur date>]]
```

---

## Templates

`templates/edition-XXX.md` is the template used by `publish_edition.sh` to create new edition drafts. It uses placeholder tokens (`_ED_NUM_`, `_ED_DATE_`, etc.) that are substituted automatically.
