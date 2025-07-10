## Update manifests

Envoy use a Jinja2 template to store its `envoy.yaml` and applies during its deployments. This is stored in `src/templates`. Envoy-related manifests are part of the KFP manifests. The process for updating them is:


### Spot the differences between versions of code files

1. Clone [Kubeflow pipelines](https://github.com/kubeflow/pipelines) repo locally.
2. Run a `git diff` command between the two versions of the upstream envoy configuration code:
    ```bash
    git diff <current-tag> <target-tag> -- third_party/metadata_envoy/ > envoy.diff
    ```
3. Look for changes in the code. This is the source for updating the template `envoy.yaml.j2`.

### Things to pay attention
* Dockerfile changes are not relevant since the charm uses the image this Dockerfile produces.
* In order to update the charm's deployment (image, ENV variables, services etc), build and compare the kfp manifests as instructed in the [kfp-operators CONTRIBUTING.md file](https://github.com/canonical/kfp-operators/blob/main/CONTRIBUTING.md#spot-the-differences-between-versions-of-a-manifest-file) and update according to envoy-related changes.


## How to Manage Python Dependencies and Environments


### Prerequisites

`tox` is the only tool required locally, as `tox` internally installs and uses `poetry`, be it to manage Python dependencies or to run `tox` environments. To install it: `pipx install tox`.

Optionally, `poerty` can be additionally installed independently just for the sake of running Python commands locally outside of `tox` during debugging/development. To install it: `pipx install poetry`.


### Updating Dependencies

To add/update/remove any dependencies and/or to upgrade Python, simply:

1. add/update/remove such dependencies to/in/from the desired group(s) below `[tool.poetry.group.<your-group>.dependencies]` in `pyproject.toml`, and/or upgrade Python itself in `requires-python` under `[project]`

    _⚠️ dependencies for the charm itself are also defined as dependencies of a dedicated group called `charm`, specifically below `[tool.poetry.group.charm.dependencies]`, and not as project dependencies below `[project.dependencies]` or `[tool.poetry.dependencies]` ⚠️_

2. run `tox -e update-requirements` to update the lock file

    by this point, `poerty`, through `tox`, will let you know if there are any dependency conflicts to solve.

3. optionally, if you also want to update your local environment for running Python commands/scripts yourself and not through tox, see [Running Python Environments](#running-python-environments) below


### Running `tox` Environments

To run `tox` environments, either locally for development or in CI workflows for testing, ensure to have `tox` installed first and then simply run your `tox` environments natively (e.g.: `tox -e lint`). `tox` will internally first install `poetry` and then rely on it to install and run its environments.


### Running Python Environments

To run Python commands locally for debugging/development from any environments built from any combinations of dependency groups without relying on `tox`:
1. ensure you have `poetry` installed
2. install any required dependency groups: `poetry install --only <your-group-a>,<your-group-b>` (or all groups, if you prefer: `poetry install --all-groups`)
3. run Python commands via poetry: `poetry run python3 <your-command>`
