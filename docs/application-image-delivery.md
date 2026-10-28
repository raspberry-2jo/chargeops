# Application images and CSMS Canary

The root Jenkins pipeline builds selected applications and publishes immutable build
tags. When environment CD is enabled on the publishing branch, it always builds
CSMS as the existing Dev verification anchor. `prepare --images "$BUILD_IMAGES"`
resolves the selected images once and records their digests in the candidate.
The verification receipt covers the entire candidate, including these images.
Unselected application images remain unchanged.

Dev currently hosts only CSMS. Its existing integration check still verifies CSMS;
it does not runtime-test notifier, ai-gateway, chatops-bot, vcharger or collector.
Those applications receive the pipeline's source/build checks and normal Ops
Deployment readiness checks. The source PR lists their exact deployment digests
and this verification limit.

After source approval and merge, promotion updates the selected image overrides in
`overlays/prod/kustomization.yaml`, inherited by the Ops Canary overlay. It also
maps the legacy `collector` image name to the built `chargeops-collector` image.
Promotion never re-resolves mutable tags. Existing checks for concurrent Git
changes, approval and receipt expiry still apply.

The Ops Argo operation syncs CSMS's existing Canary resources plus only the
Deployments/CronJobs containing approved selected images. No PVCs are added and
prune remains disabled. Only CSMS is an Argo Rollout and advances through the
existing Canary steps. Other Deployments retain their configured replica counts
and update strategies; a vcharger scaled to zero remains at zero. Completion
requires the exact application images and observed Deployment readiness.

Collector CronJob templates update in that same Sync. Existing Jobs are not
restarted and no ad hoc collection is triggered: subsequent scheduled Jobs use
the new image. A successful template Sync does not claim that a collection run
has completed successfully.

CSMS recovery still restores only CSMS's previous digest and selectively syncs
its Canary resources. Regular application images remain at their promoted values;
their failures require separate investigation/rollback. The pipeline can therefore
fail after a partial application update; the release is not an atomic rollback of
all applications. Operators should inspect the delivery receipt and Argo results.

To apply this pipeline change, push/merge the reviewed source changes, then run
the publishing Jenkins job. Use `BUILD_ALL_IMAGES=true` for the initial catch-up
release so apps whose source has not changed since the last successful build are
also rebuilt and promoted. Confirm the candidate's `images`, source approval,
the production Git diff, and `application_workloads_ready=True` in the observer.
No Ops RBAC expansion is required: CronJob application is checked using the
revision-bound Argo Sync result; Deployment reads use the existing read permissions.
