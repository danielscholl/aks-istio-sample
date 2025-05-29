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

## Getting Started

### Prerequisites

Before running this demo, you'll need to install several tools and configure your Azure environment.

#### 1. Python 3.12+

**macOS (using Homebrew):**
```bash
brew install python@3.12
```

**Windows (using winget):**
```bash
winget install Python.Python.3.12
```

**Linux (Ubuntu/Debian):**
```bash
sudo apt update
sudo apt install python3.12 python3.12-venv python3.12-pip
```

**Linux (CentOS/RHEL/Fedora):**
```bash
sudo dnf install python3.12 python3.12-pip
```

#### 2. uv (Python Package Manager)

Install uv for faster Python package management:

```bash
# macOS/Linux
curl -LsSf https://astral.sh/uv/install.sh | sh

# Windows (PowerShell)
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"

# Alternative: using pip
pip install uv
```

#### 3. Azure CLI (≥ 2.73.0)

**macOS:**
```bash
brew install azure-cli
```

**Windows:**
```bash
# Using winget
winget install Microsoft.AzureCLI

# Or download from: https://aka.ms/azure-cli
```

**Linux:**
```bash
# Ubuntu/Debian
curl -sL https://aka.ms/InstallAzureCLIDeb | sudo bash

# CentOS/RHEL/Fedora
sudo rpm --import https://packages.microsoft.com/keys/microsoft.asc
sudo dnf install azure-cli
```

#### 4. kubectl

**macOS:**
```bash
brew install kubectl
```

**Windows:**
```bash
# Using winget
winget install Kubernetes.kubectl

# Or using Chocolatey
choco install kubernetes-cli
```

**Linux:**
```bash
# Download latest version
curl -LO "https://dl.k8s.io/release/$(curl -L -s https://dl.k8s.io/release/stable.txt)/bin/linux/amd64/kubectl"
sudo install -o root -g root -m 0755 kubectl /usr/local/bin/kubectl
```

#### 5. Azure Setup

1. **Log in to Azure:**
   ```bash
   az login
   ```

2. **Set your subscription (if you have multiple):**
   ```bash
   # List available subscriptions
   az account list --output table
   
   # Set the subscription you want to use
   az account set --subscription "your-subscription-id"
   ```

3. **Verify you have Contributor access:**
   ```bash
   # Check your role assignments
   az role assignment list --assignee $(az account show --query user.name -o tsv) --output table
   ```

   You should see "Contributor" or "Owner" role for the subscription or resource group you plan to use.

#### 6. Verify Prerequisites

The script will automatically check if all prerequisites are met when you run it:

```bash
# This will show a prerequisites check table
uv run poc.py --help
```

### Optional Tools (Auto-installed by Script)

These tools are automatically downloaded if not found:

- **Istio CLI (istioctl)** - Downloaded automatically during setup
- **Helm** - Downloaded automatically if needed

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