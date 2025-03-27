# A complete guide to running AKS with Istio with a FQDN and HTTPS

This guide will walk you through setting up an Azure Kubernetes Service (AKS) cluster with Istio, and configuring it to use a valid DNS Name and HTTPS certificate. We'll use the Gateway API (the future standard for Kubernetes traffic management) along with Let's Encrypt for automatic certificate management.

## Prerequisites

Before you begin, ensure you have the following installed:
- Azure CLI
- kubectl
- istioctl
- helm

Also, make sure you're logged into Azure:
```bash
az login
```

## Quick Start

If you want to run the entire setup automatically, you can use our script directly from GitHub:

```bash
# Run with default settings (random unique ID)
curl -s https://raw.githubusercontent.com/danielscholl/aks-istio-sample/refs/heads/main/install.sh | bash


```

The script will handle all the steps described in this guide automatically. It includes:
- Resource group and AKS cluster creation
- Istio and Gateway API installation
- Cert-manager setup
- Let's Encrypt certificate configuration
- Gateway configuration
- Sample application deployment
- Health checks and testing

**Note:** The script uses Let's Encrypt staging certificates by default. To use production certificates, you can set the environment variable before running:
```bash
export LETSENCRYPT_ISSUER_TYPE="production"
curl -s https://raw.githubusercontent.com/danielscholl/aks-istio-sample/refs/heads/main/test.sh | bash
```

## Understanding the Components

Before we start, let's understand the key components we'll be working with:

1. **[Azure Kubernetes Service (AKS)](https://learn.microsoft.com/en-us/azure/aks/intro-kubernetes)**: Our managed Kubernetes cluster
2. **[Istio](https://istio.io/latest/docs/concepts/what-is-istio/)**: A service mesh that provides traffic management, security, and observability
3. **[Gateway API](https://gateway-api.sigs.k8s.io/)**: The next-generation Kubernetes traffic management API (replacing Ingress)
4. **[Cert Manager](https://cert-manager.io/docs/)**: Automates certificate management and renewal
5. **[Let's Encrypt](https://letsencrypt.org/)**: Provides free SSL/TLS certificates

## Step 1: Create Azure Resources

First, let's create the necessary Azure resources. We'll use a unique identifier to ensure our resources don't conflict with others.

### Create Resource Group and AKS Cluster
```bash
# Set variables
RESOURCE_GROUP="istio-aks-sample"
LOCATION="eastus"
UNIQUE_ID=$(tr -dc 'a-z0-9' < /dev/urandom | head -c 5)
RESOURCE_GROUP="istio-aks-sample"
AKS_NAME="istio-${UNIQUE_ID}-aks"

# Create resource group
az group create --name $RESOURCE_GROUP --location $LOCATION 

# Create AKS cluster with managed identity and Azure CNI
az aks create \
  --resource-group $RESOURCE_GROUP \
  --name $AKS_NAME \
  --node-count 1 \
  --enable-managed-identity \
  --network-plugin azure \
  --network-policy overlay \
  --max-pods 50

# Get credentials
az aks get-credentials \
  --resource-group $RESOURCE_GROUP \
  --name $AKS_NAME \
  --overwrite-existing
```

**Key Points:**
- We use Azure CNI for better network performance and pod density
- Managed identity provides secure access to Azure resources
- The `max-pods` parameter is set to 50 to allow more pods per node

## Step 2: Install Istio and Gateway API

We'll install Istio with the demo profile, which includes the core components we need. We'll also install the Gateway API CRDs, which is the future standard for Kubernetes traffic management.

```bash
# Install Istio
istioctl install --set profile=demo -y

# Install Gateway API CRDs
kubectl apply -f https://github.com/kubernetes-sigs/gateway-api/releases/download/v1.2.1/standard-install.yaml

# Get the Istio ingress gateway IP
INGRESS_IP=$(kubectl get svc istio-ingressgateway -n istio-system -o jsonpath='{.status.loadBalancer.ingress[0].ip}')
echo "Istio ingress gateway IP: $INGRESS_IP"

# Get the node resource group (where the IP is actually created)
NODE_POOL_RESOURCE_GROUP=$(az aks show \
  --resource-group $RESOURCE_GROUP \
  --name $AKS_NAME \
  --query "nodeResourceGroup" -o tsv)

# Find the public IP resource in the node resource group
IP_NAME=$(az network public-ip list \
  --resource-group $NODE_POOL_RESOURCE_GROUP \
  --query "[?ipAddress=='${INGRESS_IP}'].name" -o tsv)

# Set DNS hostname on the IP
az network public-ip update \
  --resource-group $NODE_POOL_RESOURCE_GROUP \
  --name $IP_NAME \
  --dns-name "${UNIQUE_ID}"

# Get the FQDN
FQDN=$(az network public-ip show \
  --resource-group $NODE_POOL_RESOURCE_GROUP \
  --name $IP_NAME \
  --query dnsSettings.fqdn -o tsv)

echo "FQDN: $FQDN"
```

**Understanding the Gateway API:**
The Gateway API is the next generation of Kubernetes traffic management. Unlike the traditional Ingress API, it:
- Provides more granular control over traffic routing
- Supports multiple gateway implementations
- Has better support for modern protocols and features
- Is designed to be more extensible

## Step 3: Install Cert-Manager

Cert-manager automates the management and renewal of TLS certificates. We'll use it with Let's Encrypt to automatically obtain and renew certificates.

```bash
# Add Jetstack Helm repository
helm repo add jetstack https://charts.jetstack.io
helm repo update

# Install cert-manager
helm install cert-manager jetstack/cert-manager \
  --namespace cert-manager \
  --create-namespace \
  --version v1.17.0 \
  --set crds.enabled=true
```

**Why Cert-manager?**
- Automates certificate lifecycle management
- Integrates with multiple certificate providers
- Handles certificate renewal automatically
- Provides Kubernetes-native certificate management

## Step 4: Configure Let's Encrypt

We'll create a ClusterIssuer for Let's Encrypt. We'll use the staging environment first to avoid rate limits while testing.

```bash
# Create the ClusterIssuer for Let's Encrypt staging
kubectl apply -f - << EOF
apiVersion: cert-manager.io/v1
kind: ClusterIssuer
metadata:
  name: letsencrypt-staging
spec:
  acme:
    server: https://acme-staging-v02.api.letsencrypt.org/directory
    email: admin@${FQDN}
    privateKeySecretRef:
      name: letsencrypt-staging
    solvers:
    - http01:
        ingress:
          class: istio
EOF

# Create a Certificate resource:
kubectl apply -f - << EOF
apiVersion: cert-manager.io/v1
kind: Certificate
metadata:
  name: istio-ingressgateway-certs
  namespace: istio-system
spec:
  secretName: istio-ingressgateway-certs
  duration: 2160h # 90 days
  renewBefore: 360h # 15 days
  subject:
    organizations:
      - Example Organization
  commonName: ${FQDN}
  dnsNames:
    - ${FQDN}
  issuerRef:
    name: letsencrypt-staging
    kind: ClusterIssuer
EOF
```

**Note:** We're using the staging environment for Let's Encrypt. Once everything is working, you can switch to production by changing the issuer name to `letsencrypt-prod`.

## Step 5: Configure the Gateway

Now we'll configure the Gateway to use our certificate and handle both HTTP and HTTPS traffic. The Gateway API provides a more modern and flexible way to manage ingress traffic.

### Understanding Gateway API Objects

The Gateway API introduces several key concepts:

1. **[Gateway](https://gateway-api.sigs.k8s.io/reference/spec/#gateway.networking.k8s.io/v1.Gateway)**
   - Defines how traffic can be translated to Services within the cluster
   - Specifies the ports and protocols to listen on
   - Configures TLS termination and certificate handling
   - Controls which routes can be attached to it

2. **[HTTPRoute](https://gateway-api.sigs.k8s.io/reference/spec/#gateway.networking.k8s.io/v1.HTTPRoute)**
   - Defines how HTTP traffic should be routed to different services
   - Supports path-based and header-based routing
   - Can implement traffic splitting and mirroring
   - Provides fine-grained control over request/response handling

3. **[ReferenceGrant](https://gateway-api.sigs.k8s.io/reference/spec/#gateway.networking.k8s.io/v1beta1.ReferenceGrant)**
   - Enables cross-namespace references in Gateway API
   - Required when routing traffic to services in different namespaces
   - Implements explicit opt-in for cross-namespace references
   - Enhances security by preventing unauthorized cross-namespace routing

### Gateway Configuration

Let's apply our Gateway configuration:

```bash
# Configure the Gateway and HTTPRoute
kubectl apply -f - << EOF
apiVersion: gateway.networking.k8s.io/v1
kind: Gateway
metadata:
  name: istio
  namespace: istio-system
spec:
  gatewayClassName: istio
  addresses:
  - value: istio-ingressgateway 
    type: Hostname
  listeners:
  - name: http
    protocol: HTTP
    port: 80
    allowedRoutes:
      namespaces:
        from: All
  - name: https
    protocol: HTTPS
    port: 443
    hostname: "${FQDN}"
    tls:
      mode: Terminate
      certificateRefs:
      - kind: Secret
        name: istio-ingressgateway-certs
        namespace: istio-system
    allowedRoutes:
      namespaces:
        from: All
---
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: bookinfo
  namespace: istio-system
spec:
  parentRefs:
  - name: istio
    namespace: istio-system
  rules:
  - matches:
    - path:
        type: Exact
        value: /productpage
    - path:
        type: PathPrefix
        value: /static
    - path:
        type: Exact
        value: /login
    - path:
        type: Exact
        value: /logout
    - path:
        type: PathPrefix
        value: /api/v1/products
    backendRefs:
    - name: productpage
      namespace: sample-app
      port: 9080
---
apiVersion: gateway.networking.k8s.io/v1beta1
kind: ReferenceGrant
metadata:
  name: allow-istio-system
  namespace: sample-app
spec:
  from:
  - group: gateway.networking.k8s.io
    kind: HTTPRoute
    namespace: istio-system
  to:
  - group: ""
    kind: Service
    name: productpage
EOF
```

**Gateway Configuration Details:**
- Supports both HTTP (port 80) and HTTPS (port 443)
- Uses TLS termination with our Let's Encrypt certificate
- Routes traffic to our Bookinfo application
- Implements proper security headers and TLS settings

**Key Features of Our Configuration:**
1. **Gateway:**
   - Uses the Istio gateway class for implementation
   - Configures both HTTP and HTTPS listeners
   - Implements TLS termination with our Let's Encrypt certificate
   - Allows routes from all namespaces for flexibility

2. **HTTPRoute:**
   - Routes specific paths to the Bookinfo application
   - Supports exact and prefix-based path matching
   - Handles both static content and API endpoints
   - Routes to the productpage service in the sample-app namespace

3. **ReferenceGrant:**
   - Enables the HTTPRoute in istio-system to reference the productpage service in sample-app
   - Implements explicit cross-namespace routing permission
   - Follows the principle of least privilege

**Further Reading:**
- [Gateway API Concepts](https://gateway-api.sigs.k8s.io/concepts/)
- [Istio Gateway Implementation](https://istio.io/latest/docs/tasks/traffic-management/ingress/gateway-api/)
- [Cross-Namespace References](https://gateway-api.sigs.k8s.io/guides/cross-namespace/)

## Step 6: Deploy the Sample Application

We'll deploy the Bookinfo sample application, which is a microservices demo application that consists of multiple services. We'll use the official Istio Bookinfo sample from their GitHub repository.

```bash
# Create namespace and enable Istio injection
kubectl create namespace sample-app
kubectl label namespace sample-app istio-injection=enabled

# Deploy the Bookinfo application from Istio's GitHub repository
kubectl apply -f https://raw.githubusercontent.com/istio/istio/1.24.4/samples/bookinfo/platform/kube/bookinfo.yaml -n sample-app

# Wait for pods to be ready
kubectl wait --for=condition=Ready pods --all -n sample-app --timeout=300s

# Verify the application is running by checking the product page from within the cluster
kubectl exec "$(kubectl get pod -l app=ratings -n sample-app -o jsonpath='{.items[0].metadata.name}')" \
  -c ratings -n sample-app \
  -- curl -sS productpage:9080/productpage | grep -o "<title>.*</title>"
```

**About the Bookinfo Application:**
- Productpage: Frontend service
- Details: Product information service
- Reviews: Review service with multiple versions
- Ratings: Rating service
- All services are automatically injected with Istio sidecars

**Note:** We're using the official Istio Bookinfo sample from their GitHub repository, which ensures we're using a version that's compatible with our Istio installation.

## Step 7: Test the Setup

Finally, let's test both HTTP and HTTPS access to our application:

```bash
# Test HTTP
curl -s "http://${FQDN}/productpage" | grep -o "<title>.*</title>"

# Test HTTPS
curl -s "https://${FQDN}/productpage" | grep -o "<title>.*</title>"
```

## Understanding the Architecture

Our setup creates a modern, secure, and scalable architecture:

1. **Traffic Flow:**
   - External traffic → Azure Load Balancer → Istio Gateway → Bookinfo Services
   - TLS termination at the Gateway
   - Automatic certificate renewal via cert-manager

2. **Security Features:**
   - TLS encryption
   - Managed identities
   - Network policies
   - Service mesh security

3. **Observability:**
   - Istio provides metrics, logs, and traces
   - Gateway API provides better visibility into traffic management

## Troubleshooting

If you encounter issues:

1. **Certificate Issues:**
   - Check cert-manager logs: `kubectl logs -n cert-manager -l app=cert-manager`
   - Verify ClusterIssuer status: `kubectl get clusterissuer -o yaml`

2. **Gateway Issues:**
   - Check Gateway status: `kubectl get gateway -n istio-system`
   - Verify routes: `kubectl get httproute -n istio-system`

3. **Application Issues:**
   - Check pod status: `kubectl get pods -n sample-app`
   - View Istio sidecar logs: `kubectl logs -n sample-app <pod-name> -c istio-proxy`

## Next Steps

1. Switch to production Let's Encrypt certificates
2. Add more security policies using Istio
3. Implement traffic splitting between service versions
4. Set up monitoring and alerting
5. Configure backup and disaster recovery

## Cleanup

When you're done testing, clean up the resources:

```bash
az group delete --name $RESOURCE_GROUP --yes
```

This will remove all resources created in this guide.
