#!/bin/bash

# AKS with Istio Setup Script
# This script automates the setup of an AKS cluster with Istio and configures
# HTTPS with Let's Encrypt certificates

# Exit on any error
set -e

# --------------------------
# COLOR DEFINITIONS
# --------------------------
YELLOW='\033[1;33m'
GREEN='\033[0;32m'
RED='\033[0;31m'
BLUE='\033[0;34m'  # For TRACE logs
NC='\033[0m' # No Color

# Log level (can be set to INFO to hide TRACE messages)
LOG_LEVEL=${LOG_LEVEL:-"INFO"}  # Default to TRACE if not set

# --------------------------
# CONFIGURATION VARIABLES
# --------------------------
generate_config() {
  # Set default configuration
  SUBSCRIPTION_ID=$(az account show --query id -o tsv)
  
  # Use provided unique ID or generate a random one
  if [[ -n "$1" ]]; then
    # Use the provided unique ID
    UNIQUE_ID="$1"
    log "Using provided unique ID: $UNIQUE_ID"
  else
    # Generate a random unique ID (5 alphanumeric characters)
    UNIQUE_ID=$(tr -dc 'a-z0-9' < /dev/urandom | head -c 5)
    log "Generated random unique ID: $UNIQUE_ID"
  fi
  
  # Generate resource names using the unique ID
  RESOURCE_GROUP="aks-sample-${UNIQUE_ID}"
  LOCATION="eastus"
  AKS_NAME="aks-sample-${UNIQUE_ID}-aks"
  K8S_VERSION="1.31.6"
  NODE_COUNT=1
  ISTIO_VERSION="1.24.4"
  APP_NAMESPACE="sample-app"
  
  # Let's Encrypt configuration
  LETSENCRYPT_ISSUER_TYPE="production"  # Can be "staging" or "production"
}

# --------------------------
# UTILITY FUNCTIONS
# --------------------------

# Logging functions
log() { echo -e "${GREEN}[INFO]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }
trace() { [[ "$LOG_LEVEL" == "TRACE" ]] && echo -e "${BLUE}[TRACE]${NC} $1"; }

# Verify required arguments are provided
verify() {
  if [[ -z "$1" ]]; then
    error "$2"
  fi
}

# Check if required commands are installed
check_command() {
  if ! command -v $1 &> /dev/null; then
    error "$1 is required but not installed. Please install it first."
  fi
}

# Wait for a Kubernetes resource to be ready
wait_for_resource() {
  local resource_type=$1
  local resource_name=$2
  local namespace=$3
  local wait_msg=$4
  local success_msg=$5
  local max_retries=${6:-30}
  local retries=0

  echo -n "$wait_msg"
  while [[ $retries -lt $max_retries ]]; do
    if [[ -n "$namespace" ]]; then
      status=$(kubectl get $resource_type $resource_name -n $namespace 2>/dev/null)
    else
      status=$(kubectl get $resource_type $resource_name 2>/dev/null)
    fi
    
    if [[ $? -eq 0 ]]; then
      echo -e "\n$success_msg"
      return 0
    fi
    
    sleep 5
    echo -n "."
    ((retries++))
  done
  
  echo ""
  error "Timed out waiting for $resource_type/$resource_name"
}

# --------------------------
# PREREQUISITES CHECK
# --------------------------
check_prerequisites() {
  log "Checking prerequisites..."
  
  # Check required commands
  check_command "az"
  check_command "kubectl"
  
  # Check if logged in to Azure
  az account show &> /dev/null || error "Not logged in to Azure. Run 'az login' first."
  
  # Check istioctl
  if ! command -v istioctl &> /dev/null; then
    log "istioctl not found. Downloading and installing Istio ${ISTIO_VERSION}..."
    curl -L https://istio.io/downloadIstio | ISTIO_VERSION=${ISTIO_VERSION} sh -
    export PATH=$PWD/istio-${ISTIO_VERSION}/bin:$PATH
  fi
  
  # Check helm
  if ! command -v helm &> /dev/null; then
    log "helm not found. Installing Helm..."
    curl https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash
  fi
  
  log "Prerequisites check passed."
}

# --------------------------
# AZURE RESOURCE CREATION
# --------------------------
create_resource_group() {
  local rg_name=$1
  local location=$2
  
  verify "$rg_name" "create_resource_group-ERROR: Argument (RESOURCE_GROUP) not received"
  verify "$location" "create_resource_group-ERROR: Argument (LOCATION) not received"
  
  trace "Checking resource group '$rg_name'..."
  local result=$(az group show --name $rg_name 2>/dev/null)
  
  if [[ -z "$result" ]]; then
    log "Creating resource group '$rg_name' in '$location'..."
    az group create --name $rg_name \
      --location $location \
      --tags CREATED_BY="AKS-Istio-Script" CREATED_DATE="$(date +%Y-%m-%d)" \
      --output none
    log "Resource group created."
  else
    log "Resource group '$rg_name' already exists."
  fi
}

create_aks_cluster() {
  local rg_name=$1
  local aks_name=$2
  local node_count=$3
  local k8s_version=$4
  
  verify "$rg_name" "create_aks_cluster-ERROR: Argument (RESOURCE_GROUP) not received"
  verify "$aks_name" "create_aks_cluster-ERROR: Argument (AKS_NAME) not received"
  verify "$node_count" "create_aks_cluster-ERROR: Argument (NODE_COUNT) not received"
  verify "$k8s_version" "create_aks_cluster-ERROR: Argument (K8S_VERSION) not received"
  
  trace "Checking AKS cluster '$aks_name'..."
  local result=$(az aks show --resource-group $rg_name --name $aks_name 2>/dev/null)
  
  if [[ -z "$result" ]]; then
    log "Creating AKS cluster '$aks_name'..."
    az aks create \
      --resource-group $rg_name \
      --name $aks_name \
      --node-count $node_count \
      --enable-managed-identity \
      --kubernetes-version $k8s_version \
      --network-plugin azure \
      --network-policy azure \
      --max-pods 50 \
      --tags CREATED_BY="AKS-Istio-Script" CREATED_DATE="$(date +%Y-%m-%d)" \
      --output none
    
    log "AKS cluster created successfully."
  else
    log "AKS cluster '$aks_name' already exists."
  fi

  log "Getting credentials for AKS cluster..."
  az aks get-credentials \
    --resource-group $rg_name \
    --name $aks_name \
    --overwrite-existing \
    --output none
  
  # Wait for the cluster to be fully ready
  log "Waiting for AKS cluster to be fully ready..."
  wait_for_cluster_readiness
  
  log "AKS cluster configured and ready."
}

wait_for_cluster_readiness() {
  local max_retries=30
  local retries=0
  
  while [[ $retries -lt $max_retries ]]; do
    local cluster_status=$(az aks show --resource-group $RESOURCE_GROUP --name $AKS_NAME --query provisioningState -o tsv)
    if [[ "$cluster_status" == "Succeeded" ]]; then
      # Additional check for node readiness
      local node_status=$(kubectl get nodes -o jsonpath='{.items[0].status.conditions[?(@.type=="Ready")].status}' 2>/dev/null)
      if [[ "$node_status" == "True" ]]; then
        log "AKS cluster is ready."
        # Additional delay to ensure network infrastructure is ready
        log "Waiting for network infrastructure to be ready..."
        sleep 30
        return 0
      fi
    fi
    
    sleep 10
    echo -n "."
    ((retries++))
  done
  
  error "Timed out waiting for AKS cluster to be ready"
}

# --------------------------
# ISTIO INSTALLATION
# --------------------------
install_istio() {
  local istio_version=$1
  
  verify "$istio_version" "install_istio-ERROR: Argument (ISTIO_VERSION) not received"
  
  # Check if Istio is already installed
  if kubectl get namespace istio-system &> /dev/null; then
    if kubectl get deployment -n istio-system istio-ingressgateway &> /dev/null; then
      log "Istio is already installed."
      return 0
    fi
  fi
  
  log "Installing Gateway API CRDs..."
  kubectl apply -f https://github.com/kubernetes-sigs/gateway-api/releases/download/v1.2.1/standard-install.yaml
  
  log "Installing Istio ${istio_version} with demo profile..."
  istioctl install --set profile=demo -y
  
  log "Verifying Istio installation..."
  wait_for_resource "deployment" "istio-ingressgateway" "istio-system" \
    "Waiting for Istio gateway deployment to be ready" \
    "Istio gateway deployment is ready."
  
  # Add a delay to allow Azure Load Balancer to be provisioned
  log "Waiting for Azure Load Balancer to be provisioned..."
  sleep 60
  
  log "Istio installed successfully."
}

get_ingress_ip_and_fqdn() {
  log "Getting Istio ingress gateway IP and configuring DNS..."
  
  # Wait for the Istio ingress gateway IP to be assigned
  local max_retries=30
  local retries=0
  local ingress_ip=""
  
  while [[ $retries -lt $max_retries ]]; do
    ingress_ip=$(kubectl get svc istio-ingressgateway -n istio-system -o jsonpath='{.status.loadBalancer.ingress[0].ip}')
    if [[ -n "$ingress_ip" ]]; then
      break
    fi
    log "Waiting for Istio ingress gateway IP to be assigned... (attempt $((retries + 1))/$max_retries)"
    sleep 10
    ((retries++))
  done
  
  if [[ -z "$ingress_ip" ]]; then
    error "Could not get the Istio ingress gateway IP after $max_retries attempts"
  fi
  
  INGRESS_IP=$ingress_ip
  log "Istio ingress gateway IP: $INGRESS_IP"
  
  # Get the node resource group
  NODE_POOL_RESOURCE_GROUP=$(az aks show \
    --resource-group $RESOURCE_GROUP \
    --name $AKS_NAME \
    --query "nodeResourceGroup" -o tsv)
  
  # Find the public IP resource in the node resource group
  IP_NAME=$(az network public-ip list \
    --resource-group $NODE_POOL_RESOURCE_GROUP \
    --query "[?ipAddress=='${INGRESS_IP}'].name" -o tsv)
  
  if [[ -z "$IP_NAME" ]]; then
    error "Could not find public IP resource for IP $INGRESS_IP"
  fi
  
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
  
  log "FQDN: $FQDN"
}

# --------------------------
# CERT-MANAGER SETUP
# --------------------------
install_cert_manager() {
  log "Installing cert-manager using Helm..."
  
  # Add the Jetstack Helm repository
  helm repo add jetstack https://charts.jetstack.io
  helm repo update
  
  # Install cert-manager using Helm
  helm install cert-manager jetstack/cert-manager \
    --namespace cert-manager \
    --create-namespace \
    --version v1.17.0 \
    --set crds.enabled=true
  
  # Wait for cert-manager to be ready
  wait_for_resource "deployment" "cert-manager" "cert-manager" \
    "Waiting for cert-manager deployment" \
    "cert-manager deployment is ready."
  
  wait_for_resource "deployment" "cert-manager-cainjector" "cert-manager" \
    "Waiting for cert-manager-cainjector deployment" \
    "cert-manager-cainjector deployment is ready."
  
  wait_for_resource "deployment" "cert-manager-webhook" "cert-manager" \
    "Waiting for cert-manager-webhook deployment" \
    "cert-manager-webhook deployment is ready."
  
  log "cert-manager installed successfully."
}

create_cluster_issuer() {
  log "Creating Let's Encrypt ClusterIssuer..."
  
  # Determine the Let's Encrypt server based on issuer type
  local acme_server
  if [[ "$LETSENCRYPT_ISSUER_TYPE" == "staging" ]]; then
    acme_server="https://acme-staging-v02.api.letsencrypt.org/directory"
  else
    acme_server="https://acme-v02.api.letsencrypt.org/directory"
  fi
  
  # Apply ClusterIssuer directly
  kubectl apply -f - << EOF
apiVersion: cert-manager.io/v1
kind: ClusterIssuer
metadata:
  name: letsencrypt-${LETSENCRYPT_ISSUER_TYPE}
spec:
  acme:
    server: ${acme_server}
    email: admin@${FQDN}
    privateKeySecretRef:
      name: letsencrypt-${LETSENCRYPT_ISSUER_TYPE}
    solvers:
    - http01:
        ingress:
          class: istio
EOF
  
  # Wait for the ClusterIssuer to be ready
  wait_for_resource "clusterissuer" "letsencrypt-${LETSENCRYPT_ISSUER_TYPE}" "" \
    "Waiting for ClusterIssuer to be ready" \
    "ClusterIssuer is ready."
  
  log "Let's Encrypt ClusterIssuer created successfully."
}

create_certificate() {
  log "Creating certificate for ${FQDN}..."
  
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
    name: letsencrypt-${LETSENCRYPT_ISSUER_TYPE}
    kind: ClusterIssuer
EOF
  
  # Wait for the certificate to be ready
  wait_for_resource "certificate" "istio-ingressgateway-certs" "istio-system" \
    "Waiting for certificate to be ready" \
    "Certificate is ready."
  
  log "Certificate created successfully."
}

# --------------------------
# GATEWAY CONFIGURATION
# --------------------------
configure_gateway() {
  log "Configuring Gateway with HTTPS..."
  
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
  
  log "Gateway configured successfully."
}

# --------------------------
# SAMPLE APP DEPLOYMENT
# --------------------------
create_namespace() {
  local namespace=$1
  
  log "Creating namespace '$namespace'..."
  if ! kubectl get namespace "$namespace" &> /dev/null; then
    kubectl create namespace "$namespace"
    log "Namespace '$namespace' created successfully."
  else
    log "Namespace '$namespace' already exists."
  fi
}

deploy_sample_app() {
  log "Enabling Istio injection for namespace $APP_NAMESPACE..."
  kubectl label namespace $APP_NAMESPACE istio-injection=enabled --overwrite
  
  log "Deploying sample application (Bookinfo)..."
  kubectl apply -f https://raw.githubusercontent.com/istio/istio/${ISTIO_VERSION}/samples/bookinfo/platform/kube/bookinfo.yaml -n $APP_NAMESPACE
  
  # Wait for all Bookinfo components to be ready
  wait_for_resource "deployment" "productpage-v1" "$APP_NAMESPACE" \
    "Waiting for productpage deployment" \
    "Productpage deployment is ready."
  
  wait_for_resource "deployment" "reviews-v1" "$APP_NAMESPACE" \
    "Waiting for reviews-v1 deployment" \
    "Reviews-v1 deployment is ready."
  
  wait_for_resource "deployment" "reviews-v2" "$APP_NAMESPACE" \
    "Waiting for reviews-v2 deployment" \
    "Reviews-v2 deployment is ready."
  
  wait_for_resource "deployment" "reviews-v3" "$APP_NAMESPACE" \
    "Waiting for reviews-v3 deployment" \
    "Reviews-v3 deployment is ready."
  
  wait_for_resource "deployment" "ratings-v1" "$APP_NAMESPACE" \
    "Waiting for ratings deployment" \
    "Ratings deployment is ready."
  
  wait_for_resource "deployment" "details-v1" "$APP_NAMESPACE" \
    "Waiting for details deployment" \
    "Details deployment is ready."
  
  # Add a delay to allow for full initialization and propagation
  log "Waiting for application to fully initialize..."
  sleep 30
  
  log "Sample application deployed successfully."
}

# --------------------------
# TESTING FUNCTIONS
# --------------------------
test_setup() {
  log "Testing HTTP access to the application..."
  echo ""
  echo "Attempting to access via FQDN: http://$FQDN/productpage"
  curl -s -o /dev/null -w "Status code: %{http_code}\n" http://$FQDN/productpage
  
  echo ""
  log "Testing HTTPS access to the application..."
  echo "Attempting to access via HTTPS FQDN: https://$FQDN/productpage"
  curl -k -s -o /dev/null -w "Status code: %{http_code}\n" https://$FQDN/productpage
  
  echo ""
  log "If you received status code 200, the setup is working correctly!"
  log "You can access the application at:"
  log "  http://$FQDN/productpage"
  log "  https://$FQDN/productpage"
}

# --------------------------
# INFORMATION DISPLAY
# --------------------------
display_information() {
  echo ""
  echo -e "${GREEN}=== Setup Complete ===${NC}"
  echo ""
  echo "Resource Group: $RESOURCE_GROUP"
  echo "AKS Cluster: $AKS_NAME"
  echo "Ingress IP: $INGRESS_IP"
  echo "FQDN: $FQDN"
  echo ""
  echo "Application URLs:"
  echo "  HTTP: http://$FQDN/productpage"
  echo "  HTTPS: https://$FQDN/productpage"
  echo ""
  echo -e "${YELLOW}Cleanup Instructions:${NC}"
  echo "To delete all resources when you're done testing:"
  echo "  az group delete --name $RESOURCE_GROUP --yes"
  echo ""
}

# --------------------------
# HELP FUNCTION
# --------------------------
show_help() {
  echo "Usage: $0 [unique-id]"
  echo ""
  echo "Arguments:"
  echo "  unique-id                  Optional 5-character alphanumeric ID for resource naming"
  echo "                             If not provided, a random ID will be generated"
  echo ""
  echo "Options:"
  echo "  -h, --help                 Show this help message"
  echo ""
  echo "Examples:"
  echo "  $0                         Run with default settings (random unique ID)"
  echo "  $0 abc12                   Run with specific unique ID 'abc12'"
  echo ""
}

# --------------------------
# MAIN FUNCTION
# --------------------------
main() {
  echo -e "${GREEN}=== AKS with Istio Setup Script ===${NC}"
  echo ""
  set -x
  
  # Parse command line arguments
  UNIQUE_ID=""

  while [[ $# -gt 0 ]]; do
    case $1 in
      -h|--help)
        show_help
        exit 0
        ;;
      -*)
        echo "Unknown option: $1"
        show_help
        exit 1
        ;;
      *)
        # Check if this is a valid unique ID (5 alphanumeric characters)
        if [[ "$1" =~ ^[a-z0-9]{5}$ ]]; then
          UNIQUE_ID="$1"
          shift
        else
          echo "Error: Unique ID must be exactly 5 alphanumeric characters (a-z, 0-9)"
          show_help
          exit 1
        fi
        ;;
    esac
  done
  
  # Generate configuration
  generate_config "$UNIQUE_ID"
  
  # Check prerequisites
  check_prerequisites
  
  # Create Azure resources
  create_resource_group "$RESOURCE_GROUP" "$LOCATION"
  create_aks_cluster "$RESOURCE_GROUP" "$AKS_NAME" "$NODE_COUNT" "$K8S_VERSION"
  
  # Install and configure Istio
  install_istio "$ISTIO_VERSION"
  get_ingress_ip_and_fqdn
  
  # Create required namespaces
  create_namespace "istio-system"
  create_namespace "cert-manager"
  create_namespace "$APP_NAMESPACE"
  
  # Install cert-manager and configure certificates
  install_cert_manager
  create_cluster_issuer
  create_certificate
  
  # Configure Gateway
  configure_gateway
  
  # Deploy sample application
  deploy_sample_app
  
  # Test the setup
  test_setup
  
  # Display information
  display_information
}

# Run the script
if [[ "${BASH_SOURCE[0]}" == "${0}" ]] || [[ "${BASH_SOURCE[0]}" == "/dev/stdin" ]]; then
  main "$@"
fi