# Contributing

## Development Setup

This plugin is meant to run inside the same Python environment as OctoPrint because `setup.py` depends on OctoPrint's setuptools helpers.

Recommended local setup:

1. Create and activate a virtual environment with Python 3.8+.
2. Install OctoPrint into that environment.
3. Install this plugin in editable mode.

Example:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install OctoPrint
pip install -r requirements-dev.txt
pip install -e .[develop] --no-build-isolation
```

If you use [`task`](https://taskfile.dev/), the repo includes shortcuts for common commands:

```bash
task install
task server
task debug-server
```

## Running Locally

Start OctoPrint against a local base directory:

```bash
octoprint serve --port=5001 --basedir ~/.octoprint/
```

For more verbose logs while developing:

```bash
octoprint serve --port=5001 --basedir ~/.octoprint/ --debug
```

Then open `http://localhost:5001` and test the plugin by uploading G-code files that contain embedded thumbnail data.

## Working on the Plugin

- Main plugin code lives in [`octoprint_e3s1p_bytt_thumbnails`](octoprint_e3s1p_bytt_thumbnails).
- Repository task shortcuts live in [`Taskfile.yml`](Taskfile.yml).

Good contribution workflow:

1. Create a branch for your change.
2. Make the change and test it with a local OctoPrint instance.
3. Run any checks you use locally, such as `pytest` or formatting tools.
4. Open a pull request with a clear summary and reproduction details for bug fixes.

## Release Flow

GitHub Actions publishes the plugin zip when you push a matching version tag.

1. Update `plugin_version` in `setup.py`.
2. Commit and push the version change.
3. Push a matching tag such as `v1.0.0`.

That tag triggers the release workflow, which builds `OctoPrint-E3S1P_ByTT_thumbnails.zip` and attaches it to the GitHub Release.
