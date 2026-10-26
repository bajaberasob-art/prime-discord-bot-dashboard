# PRIME Full Redeploy Trigger

Requested full redeployment trigger for the PRIME application from the current `main` branch.

This marker does not alter application runtime code. Deployment will occur only if the repository is connected to a deployment service that watches pushes to `main`.

After the push, verify the deployed revision and startup logs in Discloud.
