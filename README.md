# AKS Policy Demo: Azure Policy + OPA External Authorization

This demo shows how to implement **two different types of policy enforcement** in Kubernetes:

1. **Admission Control** - Policies that block non-compliant resources when they're created (Azure Policy + OPA Gatekeeper)
2. **Runtime Authorization** - Policies that control HTTP requests in real-time (OPA External AuthZ + Istio)

## What You'll Learn

By running this demo, you'll understand:

- **When to use admission control vs. runtime authorization**
- **How Azure Policy integrates with OPA Gatekeeper** for cluster governance
- **How OPA External AuthZ works with Istio** for HTTP request authorization
- **The difference between policy enforcement at creation time vs. request time**

## Quick Start

```bash
# Deploy everything
uv run poc.py

# With custom settings
uv run poc.py --unique-id myid1 --location westus2
```

**Prerequisites:**
- Azure CLI logged in (`az login`)
- Python 3.12+ with uv installed
- Contributor role on Azure subscription

## What Gets Deployed

The script creates:

1. **AKS cluster** with Azure Policy addon enabled
2. **Istio service mesh** for traffic management
3. **OPA External AuthZ service** for HTTP authorization
4. **Bookinfo sample app** to test policies against
5. **HTTPS certificates** via Let's Encrypt

## Two Types of Policy Enforcement

### 1. Admission Control (Azure Policy + Gatekeeper)

**When it runs:** During resource creation (kubectl apply, helm install, etc.)

**What it does:** Blocks non-compliant Kubernetes resources before they enter the cluster

**Example:** The demo assigns a built-in Azure Policy that audits containers using forbidden sysctl interfaces.

```bash
# Check what policies are active
kubectl get constrainttemplates

# View policy violations
kubectl get constraints
```

### 2. Runtime Authorization (OPA External AuthZ)

**When it runs:** On every HTTP request to protected services

**What it does:** Allows or denies HTTP requests based on headers, paths, user identity, etc.

**Example:** The demo protects the `reviews` service - requests need an `x-user-authorized: true` header.

```bash
# This request is DENIED (no auth header)
kubectl exec -n sample-app opa-test-client -- curl reviews:9080/reviews/1

# This request is ALLOWED (has auth header)  
kubectl exec -n sample-app opa-test-client -- curl -H "x-user-authorized: true" reviews:9080/reviews/1
```

## Demo Walkthrough

### Step 1: Azure Policy in Action

After deployment, Azure Policy has assigned a built-in Kubernetes policy to your cluster:

```bash
# View the policy assignment
az policy assignment list --scope /subscriptions/<sub>/resourceGroups/<rg>/providers/Microsoft.ContainerService/managedClusters/<cluster>

# See the Gatekeeper constraint it created
kubectl get k8sazurecontainernoforbiddensysctls
```

### Step 2: OPA External AuthZ in Action

The demo labels the `reviews` service for OPA protection and deploys a simple authorization policy:

```rego
package authz

default allow := false

# Allow requests with authorization header
allow if {
    input.attributes.request.http.headers["x-user-authorized"] == "true"
}
```

Test it:

```bash
# Check OPA is running
kubectl get pods -n opa

# View the authorization policy
kubectl get configmap opa-policy -n opa -o yaml

# Watch OPA make decisions
kubectl logs -n opa deployment/opa --follow
```

## Key Differences

| Aspect | Admission Control | Runtime Authorization |
|--------|------------------|----------------------|
| **When** | Resource creation time | HTTP request time |
| **What** | Kubernetes YAML | HTTP traffic |
| **Speed** | No runtime overhead | ~1ms per request |
| **Use Cases** | Resource standards, security baselines | User auth, API access control |

## Learning More

### Modify the OPA Policy

Try changing the authorization logic:

```bash
# Edit the policy
kubectl edit configmap opa-policy -n opa

# OPA automatically reloads - test immediately
kubectl exec -n sample-app opa-test-client -- curl reviews:9080/reviews/1
```

### Add More Protected Services

Label other services for OPA protection:

```bash
# Protect the ratings service
kubectl patch deployment ratings-v1 -n sample-app --type=merge -p='{"spec":{"template":{"metadata":{"labels":{"opa-authz":"enabled"}}}}}'
```

## Troubleshooting

### Policy Not Working?

```bash
# Check Azure Policy addon
kubectl get pods -n kube-system -l app=azure-policy
kubectl get pods -n gatekeeper-system

# Check OPA External AuthZ
kubectl get pods -n opa
kubectl logs -n opa deployment/opa
```

### Can't Access Application?

```bash
# Check ingress IP
kubectl get svc -n istio-system istio-ingressgateway

# Test internal connectivity
kubectl exec -n sample-app opa-test-client -- curl productpage:9080/productpage
```

## Cleanup

```bash
# Delete everything
uv run poc.py --cleanup

# Or manually
az group delete --name <resource-group-name> --yes
```

## Files in This Repo

- `poc.py` - Main deployment script
- `install.sh` - Alternative bash script (less features)
- `opa-extauth.md` - Reference article about OPA External AuthZ
- `aks-policy.md` - Azure Policy documentation
- `sample/` - Sample Kubernetes manifests

## Why This Matters

Most Kubernetes demos show either admission control OR runtime authorization, but not both. This demo shows how they work together to provide **defense in depth**:

- **Admission control** ensures only compliant resources enter your cluster
- **Runtime authorization** protects your applications from unauthorized access

Understanding both approaches helps you build more secure and compliant Kubernetes platforms.