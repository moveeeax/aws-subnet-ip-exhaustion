# Examples

Everything here runs without AWS credentials, against the payload in
`tests/fixtures/eks-vpc.json` (three subnets of one pretend EKS VPC: a busy `/24`
for pods, a cramped `/28` for the public tier, a roomy `/20` for data).

## A plain run

```console
$ aws-subnet-ip-exhaustion --from-json tests/fixtures/eks-vpc.json
SEVERITY  SUBNET       CIDR          AZ             USED/USABLE  FREE  UTIL%  DAYS LEFT  TOP CONSUMER
--------  -----------  ------------  -------------  -----------  ----  -----  ---------  ------------
CRITICAL  public-1b    10.0.2.0/28   eu-central-1b  10/11        1     90.9   n/a        instance 5
WARN      eks-pods-1a  10.0.1.0/24   eu-central-1a  200/251      51    79.7   n/a        eks_pod 135
ok        data-1c      10.0.16.0/20  eu-central-1c  42/4091      4049  1.0    n/a        instance 26
```

`DAYS LEFT` is `n/a` because this is the first snapshot. One run can measure, it
cannot extrapolate.

## With a history

`tests/fixtures/history-losing.ndjson` holds three earlier snapshots of the pod
subnet, dropping 60 addresses every ten days. The window is widened because the
fixture is deliberately old.

```console
$ aws-subnet-ip-exhaustion --from-json tests/fixtures/eks-vpc.json \
    --history tests/fixtures/history-losing.ndjson --trend-window 3650 --no-write
...
CRITICAL  eks-pods-1a  10.0.1.0/24   eu-central-1a  200/251      51    79.7   13.3       eks_pod 135
```

The subnet is still under `--critical-pct`, and it is critical anyway: the
projection lands inside the 30-day horizon. The exact day count shifts as the
fixture's snapshots age; the classification does not.

## Only the subnets that matter

```console
$ aws-subnet-ip-exhaustion --from-json tests/fixtures/eks-vpc.json --format json \
    | jq -r '.subnets[] | select(.severity=="critical") | "\(.name) \(.free) free"'
public-1b 1 free
```

## As a scheduled gate

`ci-gate.yml` is a workflow that runs the check every morning against a real
account and fails on `critical`. It keeps the history file as a cached artifact,
which is what makes the forecast possible at all — without a history the gate can
only ever see the current percentage.
