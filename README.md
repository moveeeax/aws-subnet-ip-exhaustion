# aws-subnet-ip-exhaustion

[![ci](https://github.com/moveeeax/aws-subnet-ip-exhaustion/actions/workflows/ci.yml/badge.svg)](https://github.com/moveeeax/aws-subnet-ip-exhaustion/actions/workflows/ci.yml)
[![python](https://img.shields.io/badge/python-3.11%E2%80%933.15-blue)](pyproject.toml)
[![license](https://img.shields.io/badge/license-MIT-green)](LICENSE)

Forecast when each VPC subnet runs out of free IP addresses — and show what is
eating them.

AWS gives you `AvailableIpAddressCount` and stops there. That number has no
memory, so it cannot tell you whether the subnet lost four hundred addresses this
week or has been flat since the VPC was created, and it never says what is
consuming the space. On EKS that matters a lot: the VPC CNI hands every pod a
real ENI address, and one node group scale-out can eat a `/24` in minutes. The
first signal is usually `InsufficientFreeAddressesInSubnet` in an autoscaling
event log, three layers away from the actual cause.

This tool joins `describe_subnets` with `describe_network_interfaces`, does the
CIDR arithmetic properly, keeps a snapshot history, and turns the pair into one
number worth paging on: days to exhaustion.

## Install

```console
pip install git+https://github.com/moveeeax/aws-subnet-ip-exhaustion@v0.1.0
```

Needs Python 3.11+ and read-only EC2 permissions: `ec2:DescribeSubnets` and
`ec2:DescribeNetworkInterfaces`.

## Usage

Against a live account it is one argument: `aws-subnet-ip-exhaustion --region
eu-central-1`. The output below is the bundled offline payload, so you can
reproduce it exactly:

```console
$ aws-subnet-ip-exhaustion --from-json tests/fixtures/eks-vpc.json
SEVERITY  SUBNET       CIDR          AZ             USED/USABLE  FREE  UTIL%  DAYS LEFT  TOP CONSUMER
--------  -----------  ------------  -------------  -----------  ----  -----  ---------  ------------
CRITICAL  public-1b    10.0.2.0/28   eu-central-1b  10/11        1     90.9   n/a        instance 5
WARN      eks-pods-1a  10.0.1.0/24   eu-central-1a  200/251      51    79.7   n/a        eks_pod 184
ok        data-1c      10.0.16.0/20  eu-central-1c  42/4091      4049  1.0    n/a        instance 26

3 subnet(s): 1 critical, 1 warn, 1 ok

  public-1b: utilisation 90.9% at or above critical 90.0%
  eks-pods-1a: utilisation 79.7% at or above warn 75.0%
```

`DAYS LEFT` is `n/a` on a first run: one sample can measure, it cannot
extrapolate. Add `--history` and the same pod subnet reads differently, because
the fixture history has it losing 60 addresses every ten days:

```console
$ aws-subnet-ip-exhaustion --from-json tests/fixtures/eks-vpc.json \
    --history tests/fixtures/history-losing.ndjson --trend-window 3650 --no-write
...
CRITICAL  eks-pods-1a  10.0.1.0/24   eu-central-1a  200/251      51    79.7   13.3       eks_pod 184
```

That is the case worth having a tool for. At 79.7% the subnet is below any
sensible utilisation alarm, and it is still about two weeks from failing. (The
exact figure moves as the fixture's snapshots age — the history in a real run is
your own.)

More ways to run it:

```console
aws-subnet-ip-exhaustion --vpc-id vpc-0a1b2c3d --history ~/.cache/subnet-ips.ndjson
aws-subnet-ip-exhaustion --tag Environment=prod --format json | jq '.subnets[] | select(.severity=="critical")'
aws-subnet-ip-exhaustion --horizon-days 14 --fail-on critical
aws-subnet-ip-exhaustion --format markdown >> "$GITHUB_STEP_SUMMARY"
```

Exit codes: `0` clean, `1` findings at or above `--fail-on`, `2` the tool could
not do its job (bad arguments, unreadable history, failed API call). A traceback
is never an exit code.

## How it works

**Real capacity, not the reported number.** Total addresses come from the CIDR,
minus the five AWS reserves in every subnet, minus the ENIs actually present. A
`/24` therefore has 251 usable addresses, not 256 and not 254. When the result
disagrees with `AvailableIpAddressCount`, both numbers are reported — a gap means
something is holding addresses outside the ENI list.

**Consumers, grouped by what they are.** `InterfaceType` identifies NAT gateways,
VPC endpoints, load balancers, Lambda and EKS branch ENIs. Everything AWS reports
as a plain `interface` is classified from its description, which is where the VPC
CNI (`aws-K8S-i-…`), ELB (`ELB app/…`) and RDS ENIs hide. That is why the table
can say `eks_pod 184` instead of "12 instances and 188 mystery addresses".

**A history file, because one sample is not a trend.** With `--history <path>`
each run appends one newline-delimited JSON snapshot per subnet. From two or more
snapshots inside `--trend-window` the tool fits a least-squares line through
free-address count over time and divides what is left by the slope. Flat or
recovering subnets report `null`, never a reassuring large number.

**Severity that accounts for speed.** `warn` above `--warn-pct` (default 75),
`critical` above `--critical-pct` (default 90) — and `critical` regardless of
percentage when the projection lands inside `--horizon-days` (default 30). A
subnet at 40% losing 50 addresses a day is a bigger problem than a stable one
at 85%.

**Offline by construction.** `--save-payload` writes what was collected and
`--from-json` replays it, so every rule is testable with no credentials. The
whole test suite runs that way: no `moto`, no mocks of `boto3` internals, just
JSON fixtures and plain dicts.

## Development

```console
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
ruff check . && pytest
```

The fixture payload is generated, not hand-edited:
`python tests/fixtures/make_eks_vpc.py`. CI regenerates it and fails on a diff.

See [`examples/`](examples/) for offline runs and a scheduled CI gate that caches
the history file between runs.

## One caveat

The forecast is linear and that is a deliberate limit. Subnet consumption is
mostly step-shaped — a node group scales out, a deployment doubles its replicas,
and the line jumps. A linear fit over a sensible window is honest about the
average rate and will be wrong about the next spike, which is exactly why the
utilisation thresholds stay in place next to it. Set `--horizon-days` to the time
you actually need to add a secondary CIDR, not to the time you would like to have.

## License

MIT
