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
* In order to update the charm's deployment (image, ENV variables, services etc), build and compare the kfp manifests as instructed in the [kfp-operators CONTRIBUTING.md file](https://github.com/canonical/kfp-operators/blob/69d4a0b3942cccaf7319f0c68807cbb0b6fe1b9b/CONTRIBUTING.md#spot-the-differences-between-versions-of-a-manifest-file) and update according to envoy-related changes.
