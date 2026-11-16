# Hosted Olympus census

Public route: `https://olympus-v.org/census` (requires the ALB rule below).
The service is separate from the guild websites. It serves aggregate snapshots
only; it never exposes the local companion dashboard, roster records or secrets.
Members copy the text into `/oly censusapi paste`, with no client credentials.
Forever collection remains disabled pending official documentation.

At the initial deployment, the EC2 service passed its private health and page
checks, and the existing website `/readyz` remained HTTP 200. Public `/census`
was still HTTP 404: the `OlympusRepairAdmin` instance role denied ELB inspection
and routing operations. [iam-routing-policy.json](iam-routing-policy.json) is a
reviewable, region/resource-scoped policy for the account owner to attach to that
role. It also includes ingress access for this instance's security group so only
the ALB group can be allowed to reach port 8788. It does not grant IAM management
or permission to replace the website listener's default action.

Build from the repository root:

```sh
docker build -f deploy/census/Dockerfile -t olympus-census:policy-review .
```

Provision `/etc/olympus-census/runtime.env` as root, mode 0600, containing
`BNET_CLIENT_ID`, `BNET_CLIENT_SECRET`, and `CENSUS_TARGETS` (JSON array).
Never commit that file. Use the site's existing developer application only on
the server, subject to its registered purpose and Blizzard's current terms.
The EC2 site's configured guild slug is `olympus-v`; requested scope is US Normal.
Do not assume the requested ruleset has been validated until the adapter is enabled
against official documentation and a live summary confirms it.

Create `/var/lib/olympus-census` owned by UID/GID 10001, mode 0700. Set
`CENSUS_BIND_IP` in the deployment environment to the instance's private IPv4
address for ALB access (default is loopback), then run:

```sh
docker compose -f deploy/census/compose.yaml up -d
```

The private port 8788 must accept traffic only from the ALB security group, not
the internet. Add an HTTP instance target group on port 8788 in the same VPC,
health path `/census/healthz`, success 200. Register the existing instance. Add
an HTTPS listener rule matching host `olympus-v.org` and paths `/census`,
`/census/*`, forwarding to this new group. Do not replace the default website
target or the HTTPS listener. Keep the existing HTTP-to-HTTPS behavior.

Verify `/readyz` before and after routing. Verify `/census/healthz` and the page,
`/census/status.json`, and `/census/snapshot.txt` through HTTPS. While Forever
is disabled, status is `awaiting_official_api` and snapshot returns 503; an empty
snapshot must never be interpreted as zero members. Public requests never trigger
Blizzard calls. Gunicorn uses one collector worker with four HTTP threads.

HTTP access logging is disabled by default. Responses forbid caching. Do not
include the data directory in backups exceeding the retention period. Stop and
remove local API data if the collector is discontinued. Policy details and limits
are in [BLIZZARD-POLICY.md](../../docs/BLIZZARD-POLICY.md).

Rollback: remove only the new census listener rule, stop the `olympus-census`
compose project, deregister its target, and delete its dedicated target group
and security-group ingress rule. Guild-site containers are not modified.
