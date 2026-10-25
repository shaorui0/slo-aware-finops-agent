import json
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from aws_readonly_guard import check  # noqa: E402

HOOK = Path(__file__).parent / "aws_readonly_guard.py"

ALLOWED = [
    "ls -la",
    "echo awsome",
    "aws sts get-caller-identity",
    "aws --profile finops-ro ec2 describe-instances --region us-east-1",
    "aws ce get-cost-and-usage --time-period Start=2026-09-01,End=2026-10-01 --granularity MONTHLY --metrics UnblendedCost",
    "aws cloudwatch get-metric-statistics --namespace AWS/EC2 --metric-name CPUUtilization",
    "aws s3 ls s3://bucket/",
    "aws --output=json rds describe-db-instances | jq .",
    "aws ec2 describe-volumes && aws elbv2 describe-load-balancers",
    "/usr/local/bin/aws resourcegroupstaggingapi get-resources",
    "aws help",
    "aws ec2 help",
]

BLOCKED = [
    "aws ec2 terminate-instances --instance-ids i-1",
    "aws ec2 describe-instances; aws ec2 stop-instances --instance-ids i-1",
    "aws s3 rm s3://bucket/key",
    "aws s3 cp a s3://bucket/",
    "aws s3 sync . s3://bucket/",
    "aws --profile x iam create-user --user-name y",
    "aws ec2 create-tags --resources i-1 --tags Key=a,Value=b",
    "aws autoscaling set-desired-capacity --auto-scaling-group-name g --desired-capacity 0",
    "aws secretsmanager get-secret-value --secret-id s",
    "aws ssm get-parameter --name p --with-decryption",
    "aws configure set aws_access_key_id X",
    "bash -c 'aws ec2 terminate-instances --instance-ids i-1'",
    "eval aws ec2 terminate-instances",
    "echo i-1 | xargs aws ec2 terminate-instances --instance-ids",
    "$(echo aws) ec2 terminate-instances",
    "aws ec2",
    "aws 'unterminated",
]

CTX = "--context k3d-finops-test"
INTENT = "# INTENT: canary one workload, observed over-provisioning, expect lower request\n"

K8S_ALLOWED = [
    "kubectl get pods -n shop",
    "kubectl --context k3d-cre6630 get pods",  # reads are not restricted
    "kubectl describe deploy api-steady",
    "kubectl top pod",
    "kubectl logs deploy/api-steady -n shop",
    "kubectl rollout status deploy/api-steady",
    "kubectl version",
    "kubectl config view",
    "kubectl config get-contexts",
    "kubectl config current-context",
    "kubectl api-resources",
    "kubectl auth can-i get pods",
    "kubectl wait --for=condition=Available deploy/api-steady",
    "kubectl get --raw /api/v1/namespaces/shop/services/api-steady:8080/proxy/metrics",
    "/opt/homebrew/bin/kubectl --context=k3d-cre6630 get nodes | head",
    "ls ops/k3d/",
    "git commit -m 'kubectl delete is blocked by the hook'",
    INTENT + f"kubectl {CTX} -n shop patch deploy api-steady --type strategic -p '{{}}'",
    INTENT + "kubectl --context=k3d-finops-test apply -f ops/k3d/shop.yaml",
    INTENT + f"kubectl {CTX} scale deploy api-steady --replicas=2",
    INTENT + f"kubectl {CTX} rollout restart deploy/api-steady",
    INTENT + f"kubectl {CTX} rollout undo deploy/api-steady",
    INTENT + f"kubectl {CTX} delete pod x",
    "# INTENT: a; b && c | d\n" + f"kubectl {CTX} label ns shop a=b",
    "k3d cluster create finops-test --servers 1 --agents 0",
    "k3d cluster delete finops-test",
    "k3d cluster list",
    "k3d cluster start finops-test",
    "k3d cluster stop finops-test",
    "k3d kubeconfig get finops-test",
    "k3d version",
    "k3d image import -c finops-test app:latest",
]

K8S_BLOCKED = [
    INTENT + "kubectl --context k3d-cre6630 delete pod x",
    INTENT + "kubectl --context=k3d-cre6630 patch deploy x -p '{}'",
    INTENT + "kubectl -n shop patch deploy x -p '{}'",  # missing context
    INTENT + "kubectl delete pod x",  # missing context
    f"kubectl {CTX} patch deploy x -p '{{}}'",  # missing INTENT
    f"kubectl {CTX} apply -f x.yaml",
    f"kubectl {CTX} rollout undo deploy/x",
    "kubectl --context k3d-finops-test delete ns shop # INTENT: after the command does not count",
    INTENT + f"kubectl {CTX} get pods; kubectl delete pod x",  # second command lacks context
    INTENT + f"kubectl {CTX} exec -it pod -- sh",  # unknown verb
    INTENT + f"kubectl {CTX} port-forward pod 80:80",
    INTENT + f"kubectl {CTX} config use-context k3d-cre6630",
    INTENT + f"kubectl {CTX} drain n1 | kubectl delete node n1",
    "bash -c 'kubectl delete pod x'",
    "eval kubectl delete pod x",
    "echo pod | xargs kubectl delete pod",
    "$(echo kubectl) delete pod x",
    "kubectl 'unterminated",
    "k3d cluster delete cre6630",
    "k3d cluster delete",
    "k3d cluster delete --all",
    "k3d cluster stop cre6630",
    "k3d cluster start cre6630 finops-test",
    "k3d cluster create other",
    "k3d kubeconfig merge cre6630",
    "k3d kubeconfig get --all",
    "k3d cluster list cre6630",
    "k3d node delete x",
    "k3d image import -c cre6630 app:latest",
]


class GuardTest(unittest.TestCase):
    def test_allowed(self):
        for cmd in ALLOWED:
            with self.subTest(cmd=cmd):
                self.assertIsNone(check(cmd))

    def test_blocked(self):
        for cmd in BLOCKED:
            with self.subTest(cmd=cmd):
                self.assertIsNotNone(check(cmd))

    def test_k8s_allowed(self):
        for cmd in K8S_ALLOWED:
            with self.subTest(cmd=cmd):
                self.assertIsNone(check(cmd))

    def test_k8s_blocked(self):
        for cmd in K8S_BLOCKED:
            with self.subTest(cmd=cmd):
                self.assertIsNotNone(check(cmd))

    def test_hook_protocol_k8s(self):
        def run(cmd):
            payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": cmd}})
            return subprocess.run([sys.executable, str(HOOK)], input=payload, capture_output=True, text=True)

        self.assertEqual(run("kubectl get pods").returncode, 0)
        r = run("kubectl --context k3d-cre6630 delete pod x")
        self.assertEqual(r.returncode, 2)
        self.assertIn("k3d-finops-test", r.stderr)
        self.assertEqual(run(INTENT + f"kubectl {CTX} delete pod x").returncode, 0)

    def test_hook_protocol(self):
        def run(cmd, tool="Bash"):
            payload = json.dumps({"tool_name": tool, "tool_input": {"command": cmd}})
            return subprocess.run([sys.executable, str(HOOK)], input=payload, capture_output=True, text=True)

        self.assertEqual(run("aws ec2 describe-instances").returncode, 0)
        blocked = run("aws ec2 terminate-instances --instance-ids i-1")
        self.assertEqual(blocked.returncode, 2)
        self.assertIn("BLOCKED", blocked.stderr)
        self.assertEqual(run("aws ec2 terminate-instances", tool="Read").returncode, 0)


if __name__ == "__main__":
    unittest.main()
