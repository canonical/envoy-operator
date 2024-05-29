## Update manifests

Envoy use a Jinja2 template to store its `envoy.yaml` and applies during its deployments. This is stored in `src/templates`. Envoy-related manifests are part of the KFP manifests. The process for updating them is:

### Spot the differences between versions of manifest files

1. Install `kustomize` using the official documentation [instructions](https://kubectl.docs.kubernetes.io/installation/kustomize/)
2. Clone [Kubeflow manifests](https://github.com/kubeflow/manifests) repo locally
3. `cd` into the repo and checkout to the branch or tag of the target version.
4. Build the manifests with `kustomize` according to instructions in https://github.com/kubeflow/manifests?tab=readme-ov-file#kubeflow-pipelines.
5. Checkout to the branch or tag of the version of the current manifest
6. Build the manifest with `kustomize` (see step 4) and save the file
7. Compare both files to spot the envoy-related changes (e.g. using diff `diff kfp-manifests-vX.yaml kfp-manifests-vY.yaml > kfp-vX-vY.diff`)


### Spot the differences between versions of code files

1. Clone [Kubeflow pipelines](https://github.com/kubeflow/pipelines) repo locally
2. Run a `git diff` command between the two versions in the code of upstream envoy configuration

    ```bash
    git diff <current-tag> <target-tag> -- third_party/metadata_envoy/ > envoy.diff
    ```
3. Look for changes in the code.

Note that Dockerfile changes are not relevant since the charm uses the image this Dockerfile produces.
