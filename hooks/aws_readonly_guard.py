#!/usr/bin/env python3
"""PreToolUse hook: allow only read-only AWS CLI calls, and kubectl/k3d only on the test cluster.

Layer 1 of 2. It sees Bash commands only; SDK calls and humans are covered by
AWS-side IAM (docs/aws_account_hardening.md). Fails closed: if a command
mentions aws, kubectl or k3d and cannot be parsed, it is blocked.

kubectl: read verbs pass. Mutating verbs pass only with an explicit
`--context k3d-finops-test` AND a `# INTENT:` comment line earlier in the
command. Anything else (unknown verb, other context, no context) is blocked.
k3d: only the `finops-test` cluster may be created, deleted, started or stopped.

Exit 0 = allow, exit 2 = block (stderr is shown to the agent).
"""
import json
import re
import shlex
import sys

READ_OP = re.compile(r"^(describe|get|list|lookup|search|batch-get|head)-")

# Read verbs that still leak secrets.
DENY_OPS = {
    ("secretsmanager", "get-secret-value"),
    ("secretsmanager", "batch-get-secret-value"),
    ("ssm", "get-parameter"),
    ("ssm", "get-parameters"),
    ("ssm", "get-parameters-by-path"),
    ("ec2", "get-password-data"),
    ("kms", "get-key-policy"),
    ("ecr", "get-login-password"),
    ("ecr", "get-authorization-token"),
    ("sts", "get-session-token"),
    ("sts", "get-federation-token"),
    ("iam", "get-credential-report"),
    ("lambda", "get-function"),  # returns a presigned code URL
}

ALLOW_OPS = {
    ("s3", "ls"),
    ("sts", "get-caller-identity"),
    ("configure", "list"),
    ("configure", "list-profiles"),
}

# Global options that take a value.
VALUE_OPTS = {
    "--profile", "--region", "--output", "--query", "--endpoint-url",
    "--color", "--cli-read-timeout", "--cli-connect-timeout", "--ca-bundle",
}

SEPARATORS = re.compile(r"&&|\|\||[;|\n&]")


def check_segment(tokens):
    """Return None if allowed, else a reason string."""
    idx = next((i for i, t in enumerate(tokens) if t.rsplit("/", 1)[-1] == "aws"), None)
    if idx is None:
        return None
    rest = tokens[idx + 1:]
    args = []
    i = 0
    while i < len(rest):
        t = rest[i]
        if t in VALUE_OPTS:
            i += 2
            continue
        if t.startswith("--") and "=" in t and t.split("=", 1)[0] in VALUE_OPTS:
            i += 1
            continue
        if t.startswith("-"):
            i += 1
            continue
        args.append(t)
        i += 1
    if not args or args[0] == "help" or (len(args) > 1 and args[1] == "help"):
        return None
    if len(args) < 2:
        return f"cannot determine operation in: aws {' '.join(rest)}"
    svc, op = args[0], args[1]
    if (svc, op) in ALLOW_OPS:
        return None
    if (svc, op) in DENY_OPS:
        return f"aws {svc} {op} reads secrets; not allowed"
    if svc == "ssm" and "--with-decryption" in rest:
        return "ssm --with-decryption not allowed"
    if READ_OP.match(op):
        return None
    return f"aws {svc} {op} is not a read-only operation"


# ---------------------------------------------------------------- kubectl / k3d

TEST_CONTEXT = "k3d-finops-test"
TEST_CLUSTER = "finops-test"

K_READ = {"get", "describe", "top", "logs", "version", "api-resources", "wait", "explain"}
K_WRITE = {"apply", "create", "patch", "scale", "set", "edit", "delete", "label",
           "annotate", "replace", "cordon", "uncordon", "drain", "taint"}
K_ROLLOUT_READ = {"status", "history"}
K_ROLLOUT_WRITE = {"restart", "undo", "pause", "resume"}
K_CONFIG_READ = {"view", "get-contexts", "current-context"}
# kubectl global options that take a separate value
K_VALUE_OPTS = {"--context", "-n", "--namespace", "--kubeconfig", "--cluster", "--user",
                "-s", "--server", "--request-timeout", "--as", "--as-group", "--token", "-v", "--v"}

K3D_VALUE_OPTS = {"--config", "-c", "--port", "-p", "--servers", "--agents", "--image", "-i",
                  "--api-port", "--k3s-arg", "--timeout", "--volume", "-v", "--network",
                  "--registry-use", "--label", "-l", "--env", "-e", "--node-label"}

CMD_MENTION = re.compile(r"(^|[\s;|&(`'\"])(?:[^\s'\"]*/)?(kubectl|k3d)(?=[\s;|&)`'\"]|$)")
INTENT_LINE = re.compile(r"^\s*#\s*INTENT:\s*\S")


def _positionals(rest, value_opts):
    """Return (positional args, option tokens), skipping option values."""
    args, opts, i = [], [], 0
    while i < len(rest):
        t = rest[i]
        if t == "--":
            args.extend(rest[i + 1:])
            break
        if t in value_opts:
            opts.append(t)
            i += 2
            continue
        if t.startswith("-"):
            opts.append(t)
            i += 1
            continue
        args.append(t)
        i += 1
    return args, opts


def _context_of(rest):
    ctx = None
    for i, t in enumerate(rest):
        if t == "--context" and i + 1 < len(rest):
            ctx = rest[i + 1]
        elif t.startswith("--context="):
            ctx = t.split("=", 1)[1]
    return ctx


def check_kubectl(rest, intent_seen):
    args, _ = _positionals(rest, K_VALUE_OPTS)
    if not args:
        return None  # bare `kubectl` / `kubectl --help`
    verb = args[0]
    sub = args[1] if len(args) > 1 else ""
    if verb in K_READ or verb == "help" or (verb == "rollout" and sub in K_ROLLOUT_READ) \
            or (verb == "config" and sub in K_CONFIG_READ) or (verb == "auth" and sub == "can-i"):
        return None
    is_write = verb in K_WRITE or (verb == "rollout" and sub in K_ROLLOUT_WRITE)
    if not is_write:
        return f"kubectl {f'{verb} {sub}'.strip()} is neither a known read verb nor an allowed mutating verb"
    label = f"{verb} {sub}".strip() if verb == "rollout" else verb
    ctx = _context_of(rest)
    if ctx != TEST_CONTEXT:
        return (f"kubectl {label} needs an explicit --context {TEST_CONTEXT} "
                f"(got {ctx!r}); mutations are only allowed on the local k3d test cluster")
    if not intent_seen:
        return f"kubectl {label} needs a '# INTENT: <why>' comment line before the command"
    return None


def check_k3d(rest):
    args, opts = _positionals(rest, K3D_VALUE_OPTS)
    if not args or args[0] in ("version", "help"):
        return None
    if args[0] == "kubeconfig":
        names = args[2:]
        if any(o in ("--all", "-a") for o in opts) or any(n != TEST_CLUSTER for n in names):
            return f"k3d kubeconfig may only name {TEST_CLUSTER}"
        if len(args) > 1 and args[1] in ("get", "merge", "write") and not names:
            return f"k3d kubeconfig {args[1]} must name {TEST_CLUSTER}"
        return None
    if args[0] == "cluster" and len(args) > 1:
        verb, names = args[1], args[2:]
        if verb in ("list", "get"):
            return None if all(n == TEST_CLUSTER for n in names) else f"k3d cluster {verb} may only name {TEST_CLUSTER}"
        if verb in ("create", "delete", "start", "stop"):
            if verb != "create" and any(o in ("--all", "-a") for o in opts):
                return f"k3d cluster {verb} --all is not allowed"
            if names != [TEST_CLUSTER]:
                return f"k3d cluster {verb} must name exactly {TEST_CLUSTER} (got {names or 'no cluster'})"
            return None
    if args[0] == "image" and len(args) > 1 and args[1] == "import":
        cl = [rest[i + 1] for i, t in enumerate(rest[:-1]) if t in ("-c", "--cluster")]
        if cl == [TEST_CLUSTER]:
            return None
    return f"k3d {' '.join(args[:2])} is not allowed (only cluster ops on {TEST_CLUSTER})"


def check_k8s(command):
    if not CMD_MENTION.search(command):
        return None
    if "$(" in command or "`" in command or re.search(r"\b(eval|xargs)\b", command):
        return "kubectl/k3d inside eval/xargs/command substitution cannot be verified"
    intent_seen = False
    for line in command.split("\n"):
        if INTENT_LINE.match(line):
            intent_seen = True
            continue
        if line.lstrip().startswith("#"):
            continue
        for seg in re.split(r"&&|\|\||[;|&]", line):
            seg = seg.strip()
            if not seg:
                continue
            try:
                tokens = shlex.split(seg, comments=True)
            except ValueError:
                return "unparseable command mentioning kubectl/k3d"
            if tokens and tokens[0] in ("sh", "bash", "zsh") and "-c" in tokens and CMD_MENTION.search(seg):
                return "kubectl/k3d inside a sub-shell cannot be verified"
            idx = next((i for i, t in enumerate(tokens) if t.rsplit("/", 1)[-1] in ("kubectl", "k3d")), None)
            if idx is None:
                continue
            tool = tokens[idx].rsplit("/", 1)[-1]
            reason = check_kubectl(tokens[idx + 1:], intent_seen) if tool == "kubectl" else check_k3d(tokens[idx + 1:])
            if reason:
                return reason
    return None


def check(command):
    return check_aws(command) or check_k8s(command)


def check_aws(command):
    if not re.search(r"(^|[^\w-])aws([^\w-]|$)", command):
        return None
    if "$(" in command or "`" in command or re.search(r"\b(eval|xargs)\b", command):
        return "aws inside eval/xargs/command substitution cannot be verified"
    for seg in SEPARATORS.split(command):
        seg = seg.strip()
        if not seg:
            continue
        try:
            tokens = shlex.split(seg, comments=True)
        except ValueError:
            return "unparseable command mentioning aws"
        if tokens and tokens[0] in ("sh", "bash", "zsh") and "-c" in tokens:
            return "aws inside a sub-shell cannot be verified"
        reason = check_segment(tokens)
        if reason:
            return reason
    return None


def main():
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        return 0
    if payload.get("tool_name") != "Bash":
        return 0
    reason = check(payload.get("tool_input", {}).get("command", ""))
    if reason:
        hint = ("Mutations need --context k3d-finops-test plus a '# INTENT:' line; k3d may only touch finops-test."
                if re.match(r"(kubectl|k3d)", reason) else "This project is read-only against AWS.")
        print(f"[aws_readonly_guard] BLOCKED: {reason}. {hint}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
