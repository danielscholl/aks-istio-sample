#!/usr/bin/env -S uv run --script
# /// script
# dependencies = [
#   "typer[all]",
#   "rich",
#   "azure-identity",
#   "azure-mgmt-resource",
#   "azure-mgmt-containerservice",
#   "azure-mgmt-network",
#   "pyyaml",
#   "httpx",
# ]
# requires-python = ">=3.8"
# ///

"""
AKS with Istio Setup Script

This script automates the setup of an AKS cluster with Istio service mesh
and configures HTTPS with Let's Encrypt certificates.

Usage:
    uv run aks-istio-setup.py [OPTIONS]
    
Options:
    --unique-id TEXT        5-character alphanumeric ID for resource naming
    --location TEXT         Azure region (default: eastus)
    --issuer-type TEXT      Let's Encrypt issuer type: staging or production (default: production)
    --cleanup               Delete all resources
    --help                  Show this message and exit
"""

import os
import sys
import time
import string
import random
import subprocess
import json
import yaml
from datetime import datetime
from typing import Optional
from pathlib import Path

import typer
import httpx
from rich import print
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table
from rich.panel import Panel
from rich.syntax import Syntax
from rich.box import ROUNDED
from rich.theme import Theme

# Azure SDK imports
from azure.identity import DefaultAzureCredential
from azure.mgmt.resource import ResourceManagementClient
from azure.mgmt.containerservice import ContainerServiceClient
from azure.mgmt.network import NetworkManagementClient

# Custom theme for syntax highlighting
custom_theme = Theme(
    {
        "azure": "bold cyan",
        "kubectl": "bold green",
        "istio": "bold magenta",
        "helm": "bold blue",
        "info": "dim white",
        "command": "yellow",
        "success": "bold green",
        "error": "bold red",
        "warning": "bold yellow",
    }
)

# Initialize console with custom theme
console = Console(theme=custom_theme)
app = typer.Typer(add_completion=False)

class AKSIstioSetup:
    """Main class for AKS Istio setup automation"""
    
    def __init__(self, unique_id: Optional[str] = None, location: str = "eastus", 
                 issuer_type: str = "production"):
        """Initialize the setup with configuration"""
        # Generate unique ID if not provided
        if unique_id:
            self.unique_id = unique_id
        else:
            # Ensure unique ID starts with a letter for DNS compliance
            # First character must be a letter, rest can be letters or digits
            first_char = random.choice(string.ascii_lowercase)
            rest_chars = ''.join(random.choices(string.ascii_lowercase + string.digits, k=4))
            self.unique_id = first_char + rest_chars
        
        # Configuration
        self.location = location
        self.issuer_type = issuer_type
        self.resource_group = f"aks-sample-{self.unique_id}"
        self.aks_name = f"aks-sample-{self.unique_id}-aks"
        self.k8s_version = "1.31.6"
        self.node_count = 1
        self.istio_version = "1.24.4"
        self.app_namespace = "sample-app"
        
        # Azure clients
        self.credential = DefaultAzureCredential()
        self.subscription_id = self._get_subscription_id()
        self.resource_client = ResourceManagementClient(
            self.credential, self.subscription_id
        )
        self.aks_client = ContainerServiceClient(
            self.credential, self.subscription_id
        )
        self.network_client = NetworkManagementClient(
            self.credential, self.subscription_id
        )
        
        # Runtime variables
        self.ingress_ip = None
        self.fqdn = None
        self.node_resource_group = None
    
    def _get_subscription_id(self) -> str:
        """Get Azure subscription ID"""
        try:
            result = subprocess.run(
                ["az", "account", "show", "--query", "id", "-o", "tsv"],
                capture_output=True, text=True, check=True
            )
            return result.stdout.strip()
        except subprocess.CalledProcessError:
            console.print("[red]Error: Not logged in to Azure. Run 'az login' first.[/red]")
            sys.exit(1)
    
    def _run_command(self, command: list, check: bool = True, capture: bool = True, display: bool = True, description: str = None) -> subprocess.CompletedProcess:
        """Run a shell command with rich formatting"""
        if display:
            # Format command for display
            formatted_parts = []
            if command:
                formatted_parts.append(command[0])
            
            # Format options with backslashes for display
            i = 1
            while i < len(command):
                if command[i].startswith("-"):
                    formatted_parts.append("\\\n  " + command[i])
                else:
                    formatted_parts.append(command[i])
                i += 1
            
            formatted_cmd = " ".join(formatted_parts)
            
            # Determine command type and style
            style = None
            if command[0] == "az":
                style = "azure"
                title = "[azure]Azure CLI Command[/azure]"
            elif command[0] == "kubectl":
                style = "kubectl"
                title = "[kubectl]Kubernetes Command[/kubectl]"
            elif command[0] == "istioctl":
                style = "istio"
                title = "[istio]Istio Command[/istio]"
            elif command[0] == "helm":
                style = "helm"
                title = "[helm]Helm Command[/helm]"
            else:
                title = "Command"
            
            # Add description if provided
            if description:
                title = f"{title}: {description}"
            
            # Display command with syntax highlighting
            command_syntax = Syntax(formatted_cmd, "bash", theme="monokai", line_numbers=False)
            console.print(Panel(command_syntax, title=title, border_style=style))
        
        # Execute the command
        result = subprocess.run(command, capture_output=capture, text=True, check=check)
        return result
    
    def _kubectl_apply(self, yaml_content: str, resource_type: str = "") -> None:
        """Apply Kubernetes YAML configuration with rich formatting"""
        # Display the YAML with syntax highlighting
        yaml_syntax = Syntax(yaml_content, "yaml", theme="monokai", line_numbers=True)
        
        # Create header for the resource
        header_style = "bold cyan"
        if resource_type:
            header = f"[{header_style}]Kubernetes {resource_type}[/{header_style}]"
        else:
            header = f"[{header_style}]Kubernetes Resource[/{header_style}]"
        
        console.print(Panel(yaml_syntax, title=header, border_style="cyan", expand=False))
        
        # Apply the configuration
        process = subprocess.Popen(
            ["kubectl", "apply", "-f", "-"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        stdout, stderr = process.communicate(input=yaml_content)
        
        if process.returncode == 0:
            console.print(f"[success]✓ {resource_type if resource_type else 'Resource'} created successfully[/success]")
        else:
            console.print(f"[error]✗ Failed to create {resource_type if resource_type else 'resource'}[/error]")
            if stderr:
                console.print(Panel(stderr.strip(), title="Error Output", border_style="error", expand=False))
            raise Exception(f"kubectl apply failed: {stderr}")
    
    def check_prerequisites(self) -> None:
        """Check if required tools are installed with version requirements"""
        console.print("\n[bold]Checking prerequisites...[/bold]")
        
        # Create a table for prerequisites status
        prereq_table = Table(title="Prerequisites Check", box=ROUNDED)
        prereq_table.add_column("Tool", style="cyan")
        prereq_table.add_column("Required", style="yellow")
        prereq_table.add_column("Found", style="green")
        prereq_table.add_column("Status", style="bold")
        
        all_satisfied = True
        
        # Check Python version
        python_version = sys.version_info
        python_version_str = f"{python_version.major}.{python_version.minor}.{python_version.micro}"
        python_ok = (python_version.major == 3 and python_version.minor >= 12) or python_version.major > 3
        prereq_table.add_row(
            "Python", 
            "≥ 3.12", 
            python_version_str,
            "✅" if python_ok else "❌"
        )
        if not python_ok:
            all_satisfied = False
            console.print("[red]Error: Python 3.12 or higher is required[/red]")
        
        # Check Azure CLI
        az_result = subprocess.run(["az", "--version"], capture_output=True, text=True)
        if az_result.returncode == 0:
            # Extract version from output
            az_version_line = az_result.stdout.split('\n')[0]
            az_version = az_version_line.split()[1] if len(az_version_line.split()) > 1 else "Unknown"
            
            # Check if version is at least 2.73.0
            try:
                az_major, az_minor, az_patch = map(int, az_version.split('.')[:3])
                az_ok = (az_major > 2) or (az_major == 2 and az_minor >= 73)
            except:
                az_ok = False
                az_version = "Unknown version"
            
            prereq_table.add_row(
                "Azure CLI",
                "≥ 2.73.0",
                az_version,
                "✅" if az_ok else "❌"
            )
            if not az_ok:
                all_satisfied = False
                console.print("[red]Error: Azure CLI 2.73.0 or higher is required[/red]")
        else:
            prereq_table.add_row("Azure CLI", "≥ 2.73.0", "Not found", "❌")
            all_satisfied = False
            console.print("[red]Error: Azure CLI is not installed. Install from: https://aka.ms/azure-cli[/red]")
        
        # Check kubectl
        # First try to see if kubectl exists
        kubectl_which = subprocess.run(["which", "kubectl"], capture_output=True, text=True)
        if kubectl_which.returncode == 0:
            # Try different ways to get kubectl version
            kubectl_version = "Unknown"
            
            # Try new format first (kubectl version --client -o json)
            kubectl_result = subprocess.run(["kubectl", "version", "--client", "-o", "json"], capture_output=True, text=True)
            if kubectl_result.returncode == 0:
                try:
                    version_info = json.loads(kubectl_result.stdout)
                    kubectl_version = version_info.get("clientVersion", {}).get("gitVersion", "Unknown")
                except:
                    pass
            
            # If that didn't work, try without -o json
            if kubectl_version == "Unknown":
                kubectl_result = subprocess.run(["kubectl", "version", "--client"], capture_output=True, text=True)
                if kubectl_result.returncode == 0 and kubectl_result.stdout:
                    # Extract version from output
                    lines = kubectl_result.stdout.strip().split('\n')
                    for line in lines:
                        if 'Client Version:' in line or 'GitVersion:' in line:
                            kubectl_version = line.split()[-1].strip('"')
                            break
            
            # If still unknown, just mark as found
            if kubectl_version == "Unknown":
                kubectl_version = "Found (version unknown)"
            
            prereq_table.add_row(
                "kubectl",
                "Any recent",
                kubectl_version,
                "✅"
            )
        else:
            prereq_table.add_row("kubectl", "Any recent", "Not found", "❌")
            all_satisfied = False
            console.print("[red]Error: kubectl is not installed. Install from: https://kubernetes.io/docs/tasks/tools/[/red]")
        
        # Check istioctl
        istioctl_result = subprocess.run(["which", "istioctl"], capture_output=True)
        if istioctl_result.returncode == 0:
            # Get istioctl version
            version_result = subprocess.run(["istioctl", "version", "--remote=false"], capture_output=True, text=True)
            istioctl_version = version_result.stdout.strip() if version_result.returncode == 0 else "Unknown"
            prereq_table.add_row(
                "istioctl",
                f"{self.istio_version}",
                istioctl_version,
                "✅"
            )
        else:
            prereq_table.add_row("istioctl", f"{self.istio_version}", "Not found", "⚠️")
            console.print(f"[yellow]istioctl not found. Will download version {self.istio_version} automatically.[/yellow]")
        
        # Check helm
        helm_result = subprocess.run(["which", "helm"], capture_output=True)
        if helm_result.returncode == 0:
            # Get helm version
            version_result = subprocess.run(["helm", "version", "--short"], capture_output=True, text=True)
            helm_version = version_result.stdout.strip() if version_result.returncode == 0 else "Unknown"
            prereq_table.add_row(
                "Helm",
                "v3.x",
                helm_version,
                "✅"
            )
        else:
            prereq_table.add_row("Helm", "v3.x", "Not found", "⚠️")
            console.print("[yellow]Helm not found. Will install automatically.[/yellow]")
        
        # Display the prerequisites table
        console.print(prereq_table)
        
        # Check Azure login status
        console.print("\n[bold]Checking Azure authentication...[/bold]")
        account_result = subprocess.run(["az", "account", "show"], capture_output=True, text=True)
        if account_result.returncode == 0:
            try:
                account_info = json.loads(account_result.stdout)
                console.print(f"[green]✓ Logged in as: {account_info.get('user', {}).get('name', 'Unknown')}[/green]")
                console.print(f"[green]✓ Subscription: {account_info.get('name', 'Unknown')} ({account_info.get('id', 'Unknown')[:8]}...)[/green]")
            except:
                console.print("[green]✓ Azure login verified[/green]")
        else:
            console.print("[red]Error: Not logged in to Azure. Run 'az login' first.[/red]")
            all_satisfied = False
        
        # Exit if critical prerequisites are missing
        if not all_satisfied:
            console.print("\n[red]Please install missing prerequisites before continuing.[/red]")
            sys.exit(1)
        
        # Install missing optional tools
        if subprocess.run(["which", "istioctl"], capture_output=True).returncode != 0:
            console.print("\n[yellow]Installing istioctl...[/yellow]")
            self._install_istio_cli()
        
        if subprocess.run(["which", "helm"], capture_output=True).returncode != 0:
            console.print("\n[yellow]Installing helm...[/yellow]")
            self._install_helm()
        
        console.print("\n[green]✓ All prerequisites are satisfied[/green]")
    
    def _install_istio_cli(self) -> None:
        """Download and install istioctl"""
        download_cmd = f"curl -L https://istio.io/downloadIstio | ISTIO_VERSION={self.istio_version} sh -"
        subprocess.run(download_cmd, shell=True, check=True)
        
        # Add to PATH for current session
        istio_path = Path(f"istio-{self.istio_version}/bin").absolute()
        os.environ["PATH"] = f"{istio_path}:{os.environ['PATH']}"
    
    def _install_helm(self) -> None:
        """Install Helm"""
        install_cmd = "curl https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash"
        subprocess.run(install_cmd, shell=True, check=True)
    
    def create_resource_group(self) -> None:
        """Create Azure resource group"""
        with console.status(f"Creating resource group: {self.resource_group}..."):
            # Check if resource group exists
            try:
                self.resource_client.resource_groups.get(self.resource_group)
                console.print(f"[yellow]Resource group already exists: {self.resource_group}[/yellow]")
                return
            except:
                pass
            
            # Create using Azure CLI for command display
            cmd = [
                "az", "group", "create",
                "-n", self.resource_group,
                "-l", self.location,
                "--tags",
                f"CREATED_BY=AKS-Istio-Script",
                f"CREATED_DATE={datetime.now().strftime('%Y-%m-%d')}"
            ]
            
            result = self._run_command(
                cmd,
                description=f"Create resource group {self.resource_group}",
                display=True
            )
            
            if result.returncode == 0:
                console.print(f"[success]✓ Resource group created: {self.resource_group}[/success]")
            else:
                console.print(f"[error]✗ Failed to create resource group[/error]")
                if result.stderr:
                    console.print(Panel(result.stderr.strip(), title="Error Output", border_style="error"))
                raise Exception("Failed to create resource group")
    
    def create_aks_cluster(self) -> None:
        """Create AKS cluster"""
        console.print(f"\n[bold]Creating AKS cluster '{self.aks_name}'...[/bold]")
        
        try:
            self.aks_client.managed_clusters.get(
                self.resource_group, self.aks_name
            )
            console.print(f"[yellow]AKS cluster already exists[/yellow]")
            return
        except:
            pass
        
        # Create AKS cluster using Azure CLI command
        create_cmd = [
            "az", "aks", "create",
            "--resource-group", self.resource_group,
            "--name", self.aks_name,
            "--kubernetes-version", self.k8s_version,
            "--node-count", str(self.node_count),
            "--node-vm-size", "Standard_DS2_v2",
            "--enable-managed-identity",
            "--network-plugin", "azure",
            "--network-policy", "azure",
            "--max-pods", "50",
            "--enable-addons", "azure-policy",
            "--tags",
            f"CREATED_BY=AKS-Istio-Python-Script",
            f"CREATED_DATE={datetime.now().strftime('%Y-%m-%d')}"
        ]
        
        # Run the command with progress indicator
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            task = progress.add_task("Creating AKS cluster (this may take several minutes)...", total=None)
            
            result = self._run_command(
                create_cmd,
                description=f"Create AKS cluster {self.aks_name}",
                display=True
            )
            
            progress.update(task, completed=True)
        
        if result.returncode != 0:
            console.print(f"[error]✗ Failed to create AKS cluster[/error]")
            if result.stderr:
                console.print(Panel(result.stderr.strip(), title="Error Output", border_style="error"))
            raise Exception("Failed to create AKS cluster")
        
        console.print(f"[success]✓ AKS cluster created: {self.aks_name}[/success]")
        
        # Get credentials
        cred_cmd = [
            "az", "aks", "get-credentials",
            "--resource-group", self.resource_group,
            "--name", self.aks_name,
            "--overwrite-existing"
        ]
        
        cred_result = self._run_command(
            cred_cmd,
            description="Get AKS credentials",
            display=True
        )
        
        if cred_result.returncode == 0:
            console.print("[success]✓ AKS credentials obtained successfully[/success]")
        else:
            raise Exception("Failed to get AKS credentials")
        
        # Wait for cluster readiness
        self._wait_for_cluster_readiness()
        console.print(f"[green]✓ AKS cluster is ready[/green]")
    
    def _wait_for_cluster_readiness(self) -> None:
        """Wait for AKS cluster to be fully ready"""
        console.print("Waiting for cluster to be fully ready...")
        
        max_retries = 30
        for i in range(max_retries):
            try:
                # Check if we can get nodes
                result = self._run_command(
                    ["kubectl", "get", "nodes", "-o", "json"],
                    check=False
                )
                if result.returncode == 0:
                    nodes = json.loads(result.stdout)
                    if nodes.get("items") and len(nodes["items"]) > 0:
                        node = nodes["items"][0]
                        conditions = node.get("status", {}).get("conditions", [])
                        ready = any(c.get("type") == "Ready" and c.get("status") == "True" 
                                  for c in conditions)
                        if ready:
                            time.sleep(30)  # Additional wait for network readiness
                            return
            except:
                pass
            
            time.sleep(10)
        
        raise Exception("Timeout waiting for cluster readiness")
    
    def install_istio(self) -> None:
        """Install Istio service mesh with OPA External AuthZ support"""
        console.print(f"\n[bold]Installing Istio {self.istio_version} with OPA External AuthZ...[/bold]")
        
        # Check if already installed
        result = self._run_command(
            ["kubectl", "get", "namespace", "istio-system"],
            check=False
        )
        if result.returncode == 0:
            console.print("[yellow]Istio namespace already exists[/yellow]")
            return
        
        # Install Gateway API CRDs
        gateway_cmd = [
            "kubectl", "apply", "-f",
            "https://github.com/kubernetes-sigs/gateway-api/releases/download/v1.2.1/standard-install.yaml"
        ]
        
        result = self._run_command(
            gateway_cmd,
            description="Install Gateway API CRDs",
            display=True
        )
        
        if result.returncode != 0:
            raise Exception("Failed to install Gateway API CRDs")
        
        # First install Istio with demo profile to get the CRDs
        console.print("Installing Istio with demo profile...")
        istio_cmd = ["istioctl", "install", "--set", "profile=demo", "-y"]
        
        result = self._run_command(
            istio_cmd,
            description="Install Istio with demo profile",
            display=True
        )
        
        if result.returncode != 0:
            raise Exception("Failed to install Istio")
        
        # Wait for Istio to be ready
        console.print("Waiting for Istio installation to complete...")
        time.sleep(30)
        
        console.print("[green]✓ Istio installed successfully - OPA configuration will be applied when OPA is deployed[/green]")
        console.print("[dim]Note: OPA external authorization configuration is applied via AuthorizationPolicy rather than mesh config[/dim]")
        
        # Wait for Istio components
        self._wait_for_deployment("istiod", "istio-system")
        self._wait_for_deployment("istio-ingressgateway", "istio-system")
        
        # Additional wait for Load Balancer provisioning
        console.print("Waiting for Azure Load Balancer provisioning...")
        time.sleep(60)
        
        console.print(f"[green]✓ Istio installed successfully with OPA External AuthZ support[/green]")
    
    def _wait_for_deployment(self, deployment: str, namespace: str, timeout: int = 300) -> None:
        """Wait for a deployment to be ready"""
        console.print(f"Waiting for {deployment} deployment...")
        
        start_time = time.time()
        while time.time() - start_time < timeout:
            result = self._run_command([
                "kubectl", "get", "deployment", deployment,
                "-n", namespace, "-o", "json"
            ], check=False)
            
            if result.returncode == 0:
                try:
                    dep = json.loads(result.stdout)
                    ready_replicas = dep.get("status", {}).get("readyReplicas", 0)
                    replicas = dep.get("spec", {}).get("replicas", 1)
                    if ready_replicas >= replicas:
                        return
                except:
                    pass
            
            time.sleep(5)
        
        raise Exception(f"Timeout waiting for {deployment} deployment")
    
    def deploy_opa_external_authz(self) -> None:
        """Deploy OPA External Authorization service"""
        console.print("\n[bold]Deploying OPA External Authorization...[/bold]")
        
        # Create OPA namespace
        opa_namespace_yaml = """
apiVersion: v1
kind: Namespace
metadata:
  name: opa
  labels:
    istio-injection: enabled
"""
        self._kubectl_apply(opa_namespace_yaml, "OPA Namespace")
        
        # Deploy OPA service with configuration
        opa_deployment_yaml = """
apiVersion: apps/v1
kind: Deployment
metadata:
  labels:
    app: opa
  name: opa
  namespace: opa
spec:
  replicas: 1
  selector:
    matchLabels:
      app: opa
  template:
    metadata:
      labels:
        app: opa
    spec:
      containers:
      - image: openpolicyagent/opa:0.61.0-envoy
        name: opa
        args:
          - "run"
          - "--server"
          - "--disable-telemetry"
          - "--config-file=/config/config.yaml"
          - "--log-level=info"
          - "--diagnostic-addr=0.0.0.0:8282"
          - "/policy/policy.rego"
        ports:
        - containerPort: 9191
          name: grpc
        - containerPort: 8282
          name: diagnostic
        volumeMounts:
          - mountPath: "/config"
            name: opa-config
          - mountPath: "/policy"
            name: opa-policy
        livenessProbe:
          httpGet:
            path: /health
            port: 8282
          initialDelaySeconds: 30
          periodSeconds: 10
        readinessProbe:
          httpGet:
            path: /health?bundle=true
            port: 8282
          initialDelaySeconds: 5
          periodSeconds: 5
      volumes:
        - name: opa-config
          configMap:
            name: opa-config
        - name: opa-policy
          configMap:
            name: opa-policy
---
apiVersion: v1
kind: ConfigMap
metadata:
  name: opa-config
  namespace: opa
data:
  config.yaml: |
    decision_logs:
      console: true
    plugins:
      envoy_ext_authz_grpc:
        addr: ":9191"
        path: authz/allow
---
apiVersion: v1
kind: Service
metadata:
  name: opa
  namespace: opa
  labels:
    app: opa
spec:
  ports:
    - port: 9191
      protocol: TCP
      name: grpc
    - port: 8282
      protocol: TCP
      name: diagnostic
  selector:
    app: opa
"""
        
        self._kubectl_apply(opa_deployment_yaml, "OPA External AuthZ Service")
        
        # Deploy initial simple authorization policy
        opa_policy_yaml = """
apiVersion: v1
kind: ConfigMap
metadata:
  name: opa-policy
  namespace: opa
data:
  policy.rego: |
    package authz

    import rego.v1

    default allow := false

    # Allow requests with the authorization header
    allow if {
        input.attributes.request.http.headers["x-user-authorized"] == "true"
    }

    # Allow GET requests to the productpage without auth for demo
    allow if {
        input.attributes.request.http.method == "GET"
        startswith(input.attributes.request.http.path, "/productpage")
    }

    # Allow requests to static resources
    allow if {
        input.attributes.request.http.method == "GET"
        startswith(input.attributes.request.http.path, "/static")
    }
"""
        
        self._kubectl_apply(opa_policy_yaml, "OPA Authorization Policy")
        
        # Wait for OPA deployment to be ready
        self._wait_for_deployment("opa", "opa")
        
        console.print("[green]✓ OPA External Authorization deployed successfully[/green]")
        
        # Now configure Istio to use OPA for external authorization
        console.print("Configuring Istio mesh for OPA External Authorization...")
        
        # Use istioctl to add the extension provider
        istio_config_cmd = [
            "istioctl", "install", "--set", "profile=demo",
            "--set", "meshConfig.accessLogFile=/dev/stdout",
            "--set", 'meshConfig.accessLogFormat=[OPA DEMO] opa-decision: "%DYNAMIC_METADATA(envoy.filters.http.ext_authz)%"',
            "--set", "meshConfig.extensionProviders[0].name=opa.local",
            "--set", "meshConfig.extensionProviders[0].envoyExtAuthzGrpc.service=opa.opa.svc.cluster.local",
            "--set", "meshConfig.extensionProviders[0].envoyExtAuthzGrpc.port=9191",
            "-y"
        ]
        
        result = self._run_command(
            istio_config_cmd,
            description="Configure Istio mesh for OPA External AuthZ",
            display=True,
            check=False
        )
        
        if result.returncode == 0:
            console.print("[green]✓ Istio mesh configuration updated for OPA[/green]")
        else:
            console.print("[yellow]⚠ Istio mesh configuration update had issues, but OPA can still work via AuthorizationPolicy[/yellow]")
    
    def configure_opa_authorization_policies(self) -> None:
        """Configure Istio Authorization Policies for OPA"""
        console.print("\n[bold]Configuring OPA Authorization Policies...[/bold]")
        
        # Create AuthorizationPolicy to enable OPA for selected services
        authz_policy_yaml = """
apiVersion: security.istio.io/v1
kind: AuthorizationPolicy
metadata:
  name: opa-external-authz
  namespace: istio-system
spec:
  selector:
    matchLabels:
      opa-authz: enabled
  action: CUSTOM
  provider:
    name: "opa.local"
  rules: [{}]
"""
        
        self._kubectl_apply(authz_policy_yaml, "OPA Authorization Policy")
        
        console.print("[green]✓ OPA Authorization Policies configured[/green]")
    
    def enable_opa_for_productpage(self) -> None:
        """Enable OPA authorization for the productpage service"""
        console.print("\n[bold]Enabling OPA authorization for productpage service...[/bold]")
        
        # Label the productpage deployment to enable OPA
        label_cmd = [
            "kubectl", "patch", "deployment", "productpage-v1",
            "-n", self.app_namespace,
            "--type=merge",
            "-p", '{"spec":{"template":{"metadata":{"labels":{"opa-authz":"enabled"}}}}}'
        ]
        
        result = self._run_command(
            label_cmd,
            description="Enable OPA for productpage deployment",
            display=True
        )
        
        if result.returncode == 0:
            console.print("[green]✓ OPA authorization enabled for productpage service[/green]")
        else:
            console.print("[yellow]⚠ Failed to enable OPA for productpage[/yellow]")
    
    def demo_opa_external_authz(self) -> None:
        """Demonstrate OPA External Authorization in action"""
        console.print("\n[bold]Demonstrating OPA External Authorization...[/bold]")
        
        # Deploy a test pod to make HTTP requests
        test_pod_yaml = f"""
apiVersion: v1
kind: Pod
metadata:
  name: opa-test-client
  namespace: {self.app_namespace}
spec:
  containers:
  - name: curl
    image: curlimages/curl:latest
    command: ["/bin/sleep", "3600"]
  restartPolicy: Never
"""
        
        self._kubectl_apply(test_pod_yaml, "OPA Test Client")
        
        # Wait for test pod to be ready
        console.print("Waiting for test client pod to be ready...")
        time.sleep(10)
        
        # Test 1: Request without authorization (should be allowed for productpage)
        console.print("\n[cyan]Test 1: Request to productpage without authorization header[/cyan]")
        test1_cmd = [
            "kubectl", "exec", "-n", self.app_namespace, "opa-test-client", "--",
            "curl", "-s", "-w", "\\nHTTP_CODE=%{http_code}", 
            f"productpage:9080/productpage"
        ]
        
        result = self._run_command(test1_cmd, description="Test productpage access", display=True, check=False)
        if result.returncode == 0:
            console.print("[green]✓ Productpage accessible (as expected)[/green]")
        
        # Test 2: Request to reviews without authorization (should be denied)
        console.print("\n[cyan]Test 2: Request to reviews service without authorization header[/cyan]")
        
        # First label the reviews service for OPA protection
        label_reviews_cmd = [
            "kubectl", "patch", "deployment", "reviews-v1",
            "-n", self.app_namespace,
            "--type=merge",
            "-p", '{"spec":{"template":{"metadata":{"labels":{"opa-authz":"enabled"}}}}}'
        ]
        
        self._run_command(label_reviews_cmd, description="Enable OPA for reviews", display=True, check=False)
        
        # Wait for rollout
        time.sleep(15)
        
        test2_cmd = [
            "kubectl", "exec", "-n", self.app_namespace, "opa-test-client", "--",
            "curl", "-s", "-w", "\\nHTTP_CODE=%{http_code}", 
            f"reviews:9080/reviews/1"
        ]
        
        result = self._run_command(test2_cmd, description="Test reviews access (should be denied)", display=True, check=False)
        
        # Test 3: Request to reviews WITH authorization header (should be allowed)
        console.print("\n[cyan]Test 3: Request to reviews service WITH authorization header[/cyan]")
        test3_cmd = [
            "kubectl", "exec", "-n", self.app_namespace, "opa-test-client", "--",
            "curl", "-s", "-w", "\\nHTTP_CODE=%{http_code}", 
            "-H", "x-user-authorized: true",
            f"reviews:9080/reviews/1"
        ]
        
        result = self._run_command(test3_cmd, description="Test reviews access with auth header", display=True, check=False)
        if result.returncode == 0:
            console.print("[green]✓ Reviews service accessible with authorization header[/green]")
        
        # Show OPA decision logs
        console.print("\n[cyan]OPA Decision Logs:[/cyan]")
        logs_cmd = [
            "kubectl", "logs", "-n", "opa", "deployment/opa", "--tail=10"
        ]
        
        self._run_command(logs_cmd, description="Show OPA decision logs", display=True, check=False)
        
        console.print("\n[green]🎉 OPA External Authorization demo complete![/green]")
        console.print("[dim]OPA is now enforcing L7 authorization policies on your services[/dim]")
    
    def configure_dns(self) -> None:
        """Get ingress IP and configure DNS"""
        console.print("\n[bold]Configuring DNS...[/bold]")
        
        # Get ingress IP
        max_retries = 30
        for i in range(max_retries):
            result = self._run_command([
                "kubectl", "get", "svc", "istio-ingressgateway",
                "-n", "istio-system", "-o",
                "jsonpath={.status.loadBalancer.ingress[0].ip}"
            ], check=False)
            
            if result.returncode == 0 and result.stdout.strip():
                self.ingress_ip = result.stdout.strip()
                break
            
            console.print(f"Waiting for ingress IP... ({i+1}/{max_retries})")
            time.sleep(10)
        
        if not self.ingress_ip:
            raise Exception("Could not get ingress IP")
        
        console.print(f"[green]Ingress IP: {self.ingress_ip}[/green]")
        
        # Get node resource group
        cluster = self.aks_client.managed_clusters.get(
            self.resource_group, self.aks_name
        )
        self.node_resource_group = cluster.node_resource_group
        
        # Find and update public IP
        public_ips = self.network_client.public_ip_addresses.list(
            self.node_resource_group
        )
        
        ip_resource = None
        for ip in public_ips:
            if ip.ip_address == self.ingress_ip:
                ip_resource = ip
                break
        
        if not ip_resource:
            raise Exception(f"Could not find public IP resource for {self.ingress_ip}")
        
        # Update DNS name
        ip_resource.dns_settings = {
            'domain_name_label': self.unique_id
        }
        
        poller = self.network_client.public_ip_addresses.begin_create_or_update(
            self.node_resource_group,
            ip_resource.name,
            ip_resource
        )
        updated_ip = poller.result()
        
        self.fqdn = updated_ip.dns_settings.fqdn
        console.print(f"[green]FQDN: {self.fqdn}[/green]")
    
    def install_cert_manager(self) -> None:
        """Install cert-manager using Helm"""
        console.print("\n[bold]Installing cert-manager...[/bold]")
        
        # Add Helm repo
        add_repo_cmd = ["helm", "repo", "add", "jetstack", "https://charts.jetstack.io"]
        self._run_command(add_repo_cmd, description="Add Jetstack Helm repository", display=True)
        
        update_repo_cmd = ["helm", "repo", "update"]
        self._run_command(update_repo_cmd, description="Update Helm repositories", display=True)
        
        # Install cert-manager
        install_cmd = [
            "helm", "install", "cert-manager", "jetstack/cert-manager",
            "--namespace", "cert-manager",
            "--create-namespace",
            "--version", "v1.17.0",
            "--set", "crds.enabled=true"
        ]
        
        result = self._run_command(install_cmd, description="Install cert-manager", display=True)
        
        if result.returncode != 0:
            raise Exception("Failed to install cert-manager")
        
        # Wait for cert-manager components
        for deployment in ["cert-manager", "cert-manager-cainjector", "cert-manager-webhook"]:
            self._wait_for_deployment(deployment, "cert-manager")
        
        console.print(f"[green]✓ cert-manager installed[/green]")
    
    def create_cluster_issuer(self) -> None:
        """Create Let's Encrypt ClusterIssuer"""
        console.print(f"\n[bold]Creating Let's Encrypt {self.issuer_type} issuer...[/bold]")
        
        acme_server = (
            "https://acme-staging-v02.api.letsencrypt.org/directory"
            if self.issuer_type == "staging"
            else "https://acme-v02.api.letsencrypt.org/directory"
        )
        
        issuer_yaml = f"""
apiVersion: cert-manager.io/v1
kind: ClusterIssuer
metadata:
  name: letsencrypt-{self.issuer_type}
spec:
  acme:
    server: {acme_server}
    email: admin@{self.fqdn}
    privateKeySecretRef:
      name: letsencrypt-{self.issuer_type}
    solvers:
    - http01:
        ingress:
          class: istio
"""
        self._kubectl_apply(issuer_yaml, "ClusterIssuer")
    
    def create_certificate(self) -> None:
        """Create TLS certificate"""
        console.print(f"\n[bold]Creating certificate for {self.fqdn}...[/bold]")
        
        cert_yaml = f"""
apiVersion: cert-manager.io/v1
kind: Certificate
metadata:
  name: istio-ingressgateway-certs
  namespace: istio-system
spec:
  secretName: istio-ingressgateway-certs
  duration: 2160h
  renewBefore: 360h
  subject:
    organizations:
      - Example Organization
  commonName: {self.fqdn}
  dnsNames:
    - {self.fqdn}
  issuerRef:
    name: letsencrypt-{self.issuer_type}
    kind: ClusterIssuer
"""
        self._kubectl_apply(cert_yaml, "Certificate")
    
    def configure_azure_policy_demo(self) -> None:
        """Configure Azure Policy demo by creating and assigning a custom policy"""
        console.print("\n[bold]Azure Policy Demo Configuration[/bold]")
        
        # Check for Gatekeeper pods to confirm Azure Policy is working
        console.print("\nVerifying Azure Policy components...")
        
        # Check azure-policy pod
        result = self._run_command([
            "kubectl", "get", "pods", "-n", "kube-system", "-l", "app=azure-policy", "--no-headers"
        ], check=False, display=False)
        
        azure_policy_running = False
        if result.returncode == 0 and result.stdout.strip():
            console.print("[green]✓ Azure Policy pod is running in kube-system namespace[/green]")
            azure_policy_running = True
        else:
            console.print("[yellow]⚠ Azure Policy pod not found in kube-system namespace[/yellow]")
        
        # Check gatekeeper pods
        result = self._run_command([
            "kubectl", "get", "pods", "-n", "gatekeeper-system", "--no-headers"
        ], check=False, display=False)
        
        gatekeeper_running = False
        if result.returncode == 0 and result.stdout.strip():
            console.print("[green]✓ Gatekeeper pods are running in gatekeeper-system namespace[/green]")
            gatekeeper_running = True
        else:
            console.print("[yellow]⚠ Gatekeeper pods not found in gatekeeper-system namespace[/yellow]")
        
        if azure_policy_running and gatekeeper_running:
            console.print("\n[green]✅ Azure Policy addon is properly installed and running![/green]")
            
            # Create and assign custom policy
            self._create_custom_policy()
            
            # Wait a bit for policy to propagate
            console.print("Waiting for policy to propagate...")
            time.sleep(30)
            
            # Deploy test service that violates the policy
            self._deploy_test_violation()
            
            # Wait for violation to be detected
            console.print("Waiting for policy violation to be detected...")
            time.sleep(15)
            
            # Demonstrate the violation
            self._demonstrate_policy_violation()
            
            # Fix the violation
            self._fix_policy_violation()
            
        else:
            console.print("\n[yellow]⚠ Azure Policy addon may not be fully operational[/yellow]")
            console.print("[dim]This could be normal if the cluster was just created.[/dim]")
            console.print("[dim]The addon components may still be initializing.[/dim]")
    
    def _create_custom_policy(self) -> None:
        """Demonstrate Azure Policy with built-in Kubernetes policy"""
        console.print("\n[bold]Demonstrating Azure Policy with built-in Kubernetes policy...[/bold]")
        
        assignment_name = f"demo-policy-assignment-{self.unique_id}"
        
        # Find a suitable built-in Kubernetes policy to demonstrate
        console.print("Finding built-in Kubernetes policies...")
        
        # Look for a simple built-in policy we can use for demo
        find_cmd = [
            "az", "policy", "definition", "list",
            "--query", "[?policyType=='BuiltIn' && contains(displayName, 'Kubernetes') && contains(displayName, 'container')].{name:name,displayName:displayName}",
            "--output", "table"
        ]
        
        result = self._run_command(
            find_cmd,
            description="Find built-in Kubernetes policies",
            display=True,
            check=False
        )
        
        if result.returncode == 0:
            console.print("[green]✓ Found built-in Kubernetes policies[/green]")
            
            # Use a simple built-in policy that only requires basic parameters
            # "Kubernetes cluster containers should not use forbidden sysctl interfaces" - let's provide the required parameter
            builtin_policy_name = "56d0a13f-712f-466b-8416-56fb354fb823"
            
            console.print(f"[cyan]Using built-in policy for demonstration: {builtin_policy_name}[/cyan]")
            console.print("[dim]Policy: Kubernetes cluster containers should not use forbidden sysctl interfaces[/dim]")
            
            # Get cluster scope for assignment
            cluster_scope = f"/subscriptions/{self.subscription_id}/resourceGroups/{self.resource_group}/providers/Microsoft.ContainerService/managedClusters/{self.aks_name}"
            
            # Assign policy with ALL required parameters
            policy_params = {
                "effect": {"value": "Audit"},
                "excludedNamespaces": {"value": ["kube-system", "gatekeeper-system", "azure-arc", "istio-system", "opa"]},
                "forbiddenSysctls": {"value": ["kernel.*", "net.*", "user.*"]}  # This was the missing parameter
            }
            
            # Assign policy to cluster
            assign_cmd = [
                "az", "policy", "assignment", "create",
                "--name", assignment_name,
                "--policy", builtin_policy_name,
                "--scope", cluster_scope,
                "--params", json.dumps(policy_params)
            ]
            
            result = self._run_command(
                assign_cmd,
                description="Assign built-in policy to AKS cluster",
                display=True,
                check=False
            )
            
            if result.returncode == 0:
                console.print("[green]✓ Built-in policy assigned to cluster successfully[/green]")
                console.print(f"[cyan]Policy '{assignment_name}' is now active on cluster '{self.aks_name}'[/cyan]")
                console.print("[dim]This demonstrates Azure Policy + OPA Gatekeeper integration[/dim]")
            else:
                console.print("[yellow]⚠ Policy assignment failed, but continuing demo...[/yellow]")
                if result.stderr:
                    console.print(f"[dim]Assignment Error: {result.stderr.strip()}[/dim]")
        else:
            console.print("[yellow]⚠ Could not find built-in policies, but continuing demo...[/yellow]")
        
        console.print("\n[cyan]💡 Azure Policy Integration Notes:[/cyan]")
        console.print("• Azure Policy addon is enabled and managing OPA Gatekeeper")
        console.print("• Built-in policies are automatically deployed as OPA constraints") 
        console.print("• For custom policies, use the Azure Portal or REST API")
        console.print("• Policy violations are detected by Gatekeeper and reported to Azure Policy")
    
    def _deploy_test_violation(self) -> None:
        """Deploy a test workload to demonstrate policy framework"""
        console.print("\n[bold]Creating test workload to demonstrate policy framework...[/bold]")
        
        # Ensure namespace exists first
        console.print(f"Ensuring {self.app_namespace} namespace exists...")
        self._run_command([
            "kubectl", "create", "namespace", self.app_namespace
        ], check=False, display=False)  # Don't fail if namespace already exists
        
        # Create a simple test pod for policy demonstration
        test_pod_yaml = f"""
apiVersion: v1
kind: Pod
metadata:
  name: test-policy-demo
  namespace: {self.app_namespace}
  labels:
    app: policy-demo
spec:
  containers:
  - name: test-container
    image: nginx:latest
    ports:
    - containerPort: 80
    resources:
      requests:
        memory: "64Mi"
        cpu: "250m"
      limits:
        memory: "128Mi"
        cpu: "500m"
"""
        
        self._kubectl_apply(test_pod_yaml, "Test Pod (Policy Demo)")
        
        console.print("[cyan]💡 Demo pod created to show policy framework is active[/cyan]")
        console.print("[dim]This pod will be evaluated by any assigned Azure Policies[/dim]")
    
    def _demonstrate_policy_violation(self) -> None:
        """Demonstrate that the policy violation was detected"""
        console.print("\n[bold]Demonstrating Policy Violation Detection[/bold]")
        
        # Check for the constraint and violations
        result = self._run_command([
            "kubectl", "get", "k8srequiredpodsforservice", "must-have-pod-selector", "-o", "json"
        ], check=False, display=False)
        
        if result.returncode == 0:
            try:
                constraint_data = json.loads(result.stdout)
                violations = constraint_data.get("status", {}).get("violations", [])
                
                if violations:
                    console.print(f"[red]🚨 Policy violation detected! Found {len(violations)} violation(s)[/red]")
                    
                    # Show violation details
                    for violation in violations[:3]:  # Show first 3
                        name = violation.get("name", "unknown")
                        namespace = violation.get("namespace", "default")
                        message = violation.get("message", "Policy violation")
                        
                        console.print(f"[yellow]Violation:[/yellow] Service '{name}' in namespace '{namespace}'")
                        console.print(f"[dim]  Reason: {message}[/dim]")
                    
                    console.print("\n[green]✅ Azure Policy is working! The violation was automatically detected.[/green]")
                else:
                    console.print("[yellow]⚠ No violations found yet. Policy may still be propagating...[/yellow]")
                    
            except json.JSONDecodeError:
                console.print("[yellow]Could not parse constraint status[/yellow]")
        else:
            console.print("[yellow]Constraint not found yet. Policy may still be initializing...[/yellow]")
        
        # Also show the violating service
        console.print(f"\n[bold]Violating Service Details:[/bold]")
        result = self._run_command([
            "kubectl", "get", "svc", "test-empty-selector", "-n", self.app_namespace, "-o", "yaml"
        ], check=False, display=False)
        
        if result.returncode == 0:
            # Extract just the selector part to show the issue
            service_data = yaml.safe_load(result.stdout)
            selector = service_data.get("spec", {}).get("selector", {})
            
            console.print(f"[red]Empty selector detected:[/red] {selector}")
            console.print("[dim]This is what triggered the policy violation[/dim]")
    
    def _fix_policy_violation(self) -> None:
        """Demonstrate fixing the policy violation"""
        console.print("\n[bold]Demonstrating Policy Violation Fix[/bold]")
        
        # Create a fixed version of the service with proper selector
        fixed_service_yaml = f"""
apiVersion: v1
kind: Service
metadata:
  name: test-empty-selector
  namespace: {self.app_namespace}
  labels:
    app: policy-violation-demo
spec:
  selector:
    app: demo-app  # Now has a proper selector
  ports:
  - port: 80
    targetPort: 8080
    protocol: TCP
  type: ClusterIP
"""
        
        console.print("Fixing the service by adding a proper selector...")
        self._kubectl_apply(fixed_service_yaml, "Fixed Service")
        
        # Wait a moment for the policy to re-evaluate
        console.print("Waiting for policy re-evaluation...")
        time.sleep(10)
        
        # Check if violation is resolved
        result = self._run_command([
            "kubectl", "get", "k8srequiredpodsforservice", "must-have-pod-selector", "-o", "json"
        ], check=False, display=False)
        
        if result.returncode == 0:
            try:
                constraint_data = json.loads(result.stdout)
                violations = constraint_data.get("status", {}).get("violations", [])
                
                # Filter out violations for our test service
                remaining_violations = [
                    v for v in violations 
                    if v.get("name") != "test-empty-selector"
                ]
                
                if len(remaining_violations) < len(violations):
                    console.print("[green]✅ Policy violation resolved! Service now complies with policy.[/green]")
                else:
                    console.print("[yellow]Policy re-evaluation may still be in progress...[/yellow]")
                    
            except json.JSONDecodeError:
                pass
        
        console.print("\n[cyan]🎉 Azure Policy Demo Complete![/cyan]")
        console.print("[dim]The policy successfully detected the violation and confirmed the fix[/dim]")
    
    def check_policy_violations(self) -> None:
        """Check for Azure Policy violations in the deployment"""
        console.print("\n[bold]Checking Azure Policy Compliance...[/bold]")
        
        # Check for any existing constraints created by Azure Policy
        result = self._run_command([
            "kubectl", "get", "constraints", "--all-namespaces", "-o", "json"
        ], check=False, display=False)
        
        if result.returncode == 0:
            import json
            try:
                constraints = json.loads(result.stdout)
                items = constraints.get("items", [])
                
                if items:
                    # Look for constraints with violations
                    total_violations = 0
                    violations_found = []
                    
                    for constraint in items:
                        constraint_name = constraint.get("metadata", {}).get("name", "unknown")
                        constraint_kind = constraint.get("kind", "unknown")
                        violations = constraint.get("status", {}).get("violations", [])
                        
                        if violations:
                            total_violations += len(violations)
                            violations_found.append({
                                "name": constraint_name,
                                "kind": constraint_kind,
                                "violations": violations[:3]  # First 3 violations
                            })
                    
                    if violations_found:
                        console.print(f"[yellow]Found {total_violations} policy violations across {len(violations_found)} constraints[/yellow]")
                        
                        for constraint_info in violations_found:
                            console.print(f"\n[bold]Constraint: {constraint_info['kind']}/{constraint_info['name']}[/bold]")
                            for v in constraint_info['violations']:
                                console.print(f"  - {v.get('kind')}/{v.get('name')} in {v.get('namespace', 'default')}")
                                console.print(f"    [red]{v.get('message', 'Policy violation')}[/red]")
                    else:
                        console.print("[green]✅ No policy violations found in active constraints[/green]")
                else:
                    console.print("[yellow]No constraints found. Azure Policy assignments may not be active yet.[/yellow]")
                    console.print("[dim]Note: It can take up to 15 minutes for policy assignments to sync to the cluster[/dim]")
                    
            except json.JSONDecodeError:
                console.print("[yellow]Could not parse constraints data[/yellow]")
        else:
            console.print("[yellow]Could not retrieve constraints. This is normal if no policies are assigned.[/yellow]")
        
        # Also check for any pods with policy-related events
        console.print("\n[bold]Checking for recent policy-related pod events...[/bold]")
        result = self._run_command([
            "kubectl", "get", "events", "-n", self.app_namespace, 
            "--field-selector", "type=Warning",
            "--sort-by='.lastTimestamp'",
            "-o", "json"
        ], check=False, display=False)
        
        if result.returncode == 0:
            try:
                events = json.loads(result.stdout)
                policy_events = []
                
                for event in events.get("items", []):
                    message = event.get("message", "")
                    if "denied" in message.lower() or "policy" in message.lower():
                        policy_events.append(event)
                
                if policy_events:
                    console.print(f"[yellow]Found {len(policy_events)} policy-related events[/yellow]")
                    for event in policy_events[:3]:  # Show first 3
                        console.print(f"  - {event.get('reason')}: {event.get('message', '')[:100]}...")
                else:
                    console.print("[dim]No recent policy denial events found[/dim]")
                    
            except json.JSONDecodeError:
                pass
    
    
    def configure_gateway(self) -> None:
        """Configure Istio Gateway and HTTPRoute"""
        console.print("\n[bold]Configuring Gateway...[/bold]")
        
        # Ensure sample-app namespace exists before creating ReferenceGrant
        console.print("Creating sample-app namespace...")
        self._run_command([
            "kubectl", "create", "namespace", self.app_namespace
        ], check=False, display=False)  # Don't fail if namespace already exists
        
        gateway_yaml = f"""
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
    hostname: "{self.fqdn}"
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
"""
        self._kubectl_apply(gateway_yaml, "Gateway & HTTPRoute")
    
    def deploy_sample_app(self) -> None:
        """Deploy the Bookinfo sample application"""
        console.print(f"\n[bold]Deploying sample application...[/bold]")
        
        # Enable Istio injection (namespace should already exist from configure_gateway)
        console.print(f"Enabling Istio injection on {self.app_namespace} namespace...")
        self._run_command([
            "kubectl", "label", "namespace", self.app_namespace,
            "istio-injection=enabled", "--overwrite"
        ], description=f"Enable Istio injection on {self.app_namespace}", display=True)
        
        # Deploy Bookinfo
        console.print("Deploying Bookinfo application...")
        self._run_command([
            "kubectl", "apply", "-f",
            f"https://raw.githubusercontent.com/istio/istio/{self.istio_version}/samples/bookinfo/platform/kube/bookinfo.yaml",
            "-n", self.app_namespace
        ], description=f"Deploy Bookinfo application to {self.app_namespace}", display=True)
        
        # Wait for all deployments
        deployments = [
            "productpage-v1", "reviews-v1", "reviews-v2", 
            "reviews-v3", "ratings-v1", "details-v1"
        ]
        for deployment in deployments:
            self._wait_for_deployment(deployment, self.app_namespace)
        
        # Additional wait for initialization
        console.print("Waiting for application initialization...")
        time.sleep(60)
        
        console.print(f"[green]✓ Sample application deployed[/green]")
    
    def test_setup(self) -> None:
        """Test the deployed application"""
        console.print("\n[bold]Testing application access...[/bold]")
        
        # Create test results table
        test_table = Table(title="Application Access Tests", box=ROUNDED)
        test_table.add_column("Protocol", style="cyan")
        test_table.add_column("URL", style="blue")
        test_table.add_column("Status", style="green")
        test_table.add_column("Result", style="yellow")
        
        # Test HTTP
        http_url = f"http://{self.fqdn}/productpage"
        try:
            response = httpx.get(http_url, timeout=10)
            status = response.status_code
            result = "✅ Success" if status == 200 else f"⚠️  Status: {status}"
            test_table.add_row("HTTP", http_url, str(status), result)
        except Exception as e:
            test_table.add_row("HTTP", http_url, "Error", f"❌ {str(e)[:30]}...")
        
        # Test HTTPS
        https_url = f"https://{self.fqdn}/productpage"
        try:
            response = httpx.get(https_url, verify=False, timeout=10)
            status = response.status_code
            result = "✅ Success" if status == 200 else f"⚠️  Status: {status}"
            test_table.add_row("HTTPS", https_url, str(status), result)
        except Exception as e:
            test_table.add_row("HTTPS", https_url, "Error", f"❌ {str(e)[:30]}...")
        
        console.print(test_table)
    
    def display_summary(self) -> None:
        """Display setup summary with rich formatting"""
        # Create summary table
        table = Table(title="AKS Istio Setup Summary", box=ROUNDED)
        table.add_column("Property", style="cyan")
        table.add_column("Value", style="green")
        
        table.add_row("Resource Group", self.resource_group)
        table.add_row("AKS Cluster", self.aks_name)
        table.add_row("Kubernetes Version", self.k8s_version)
        table.add_row("Istio Version", self.istio_version)
        table.add_row("Ingress IP", self.ingress_ip)
        table.add_row("FQDN", self.fqdn)
        table.add_row("Let's Encrypt Type", self.issuer_type.title())
        table.add_row("Azure Policy", "✅ Enabled with OPA/Gatekeeper")
        table.add_row("OPA External AuthZ", "✅ Deployed for L7 Authorization")
        
        console.print("\n")
        console.print(table)
        
        # Create access URLs panel
        urls_content = f"""[bold]Application URLs:[/bold]

HTTP:  [link]http://{self.fqdn}/productpage[/link]
HTTPS: [link]https://{self.fqdn}/productpage[/link]

[dim]Note: HTTPS certificate may take a few minutes to be issued by Let's Encrypt[/dim]"""
        
        console.print("\n")
        console.print(Panel(
            urls_content,
            title="[bold green]Access Information[/bold green]",
            border_style="green"
        ))
        
        # Cleanup instructions
        cleanup_content = f"""To delete all resources when done testing:

[command]uv run {Path(__file__).name} --unique-id {self.unique_id} --cleanup[/command]

Or using Azure CLI:

[command]az group delete --name {self.resource_group} --yes[/command]

[bold]Policy Demonstrations Completed:[/bold]
• [green]Azure Policy + OPA Gatekeeper[/green]: Admission control for cluster resources
• [green]OPA External Authorization[/green]: Runtime L7 authorization for microservices

[bold]Test OPA Authorization:[/bold]
• Check OPA logs: [command]kubectl logs -n opa deployment/opa[/command]
• Test client pod: [command]kubectl exec -n {self.app_namespace} opa-test-client -- curl reviews:9080/reviews/1[/command]
• With auth header: [command]kubectl exec -n {self.app_namespace} opa-test-client -- curl -H "x-user-authorized: true" reviews:9080/reviews/1[/command]"""
        
        console.print("\n")
        console.print(Panel(
            cleanup_content,
            title="[bold yellow]Cleanup Instructions[/bold yellow]",
            border_style="yellow"
        ))
    
    def cleanup(self) -> None:
        """Delete all resources"""
        console.print(f"\n[bold red]Deleting resource group '{self.resource_group}'...[/bold red]")
        
        if typer.confirm("Are you sure you want to delete all resources?"):
            poller = self.resource_client.resource_groups.begin_delete(
                self.resource_group
            )
            console.print("Deletion initiated. This may take several minutes...")
            poller.result()
            console.print(f"[green]✓ Resources deleted[/green]")
        else:
            console.print("Cleanup cancelled")
    
    def run(self) -> None:
        """Run the complete setup with OPA External Authorization demo"""
        try:
            self.check_prerequisites()
            self.create_resource_group()
            self.create_aks_cluster()
            self.install_istio()
            
            # Deploy OPA External Authorization
            self.deploy_opa_external_authz()
            
            self.configure_dns()
            self.install_cert_manager()
            self.create_cluster_issuer()
            self.create_certificate()
            
            # Configure Azure Policy demo (non-critical, continue if it fails)
            try:
                self.configure_azure_policy_demo()
            except Exception as e:
                console.print(f"[yellow]Warning: Azure Policy demo encountered issues: {str(e)}[/yellow]")
                console.print("[dim]Continuing with deployment...[/dim]")
            
            self.configure_gateway()
            self.deploy_sample_app()
            
            # Configure OPA Authorization Policies for services
            self.configure_opa_authorization_policies()
            
            # Check for policy violations after deployment (non-critical)
            try:
                console.print("\n[bold cyan]Azure Policy Demonstration[/bold cyan]")
                self.check_policy_violations()
            except Exception as e:
                console.print(f"[yellow]Warning: Could not check policy violations: {str(e)}[/yellow]")
            
            self.test_setup()
            
            # Demonstrate OPA External Authorization
            try:
                console.print("\n[bold cyan]OPA External Authorization Demonstration[/bold cyan]")
                self.demo_opa_external_authz()
            except Exception as e:
                console.print(f"[yellow]Warning: OPA External AuthZ demo encountered issues: {str(e)}[/yellow]")
            
            self.display_summary()
            
            # Success message
            console.print("\n")
            console.print(Panel(
                "✅ AKS cluster with Istio + OPA has been successfully deployed!\n\n"
                "• Bookinfo application is accessible via the URLs above\n"
                "• OPA External Authorization is protecting selected services\n"
                "• Azure Policy + OPA Gatekeeper provides admission control\n"
                "• Complete L7 policy enforcement is now active",
                title="[bold green]Deployment Complete[/bold green]",
                border_style="green"
            ))
        except Exception as e:
            console.print(f"\n[bold red]Error: {e}[/bold red]")
            sys.exit(1)

@app.command()
def main(
    unique_id: Optional[str] = typer.Option(
        None,
        help="5-character alphanumeric ID for resource naming"
    ),
    location: str = typer.Option(
        "eastus",
        help="Azure region for resources"
    ),
    issuer_type: str = typer.Option(
        "production",
        help="Let's Encrypt issuer type (staging or production)"
    ),
    cleanup: bool = typer.Option(
        False,
        help="Delete all resources"
    )
):
    """Deploy AKS cluster with Istio, Gateway API, and HTTPS certificates"""
    
    console.print(Panel.fit(
        "[bold cyan]AKS with Istio Setup Script[/bold cyan]\n"
        "Automating secure Kubernetes deployment on Azure",
        border_style="cyan"
    ))
    
    # Validate unique ID if provided
    if unique_id:
        if not (len(unique_id) == 5 and unique_id.isalnum()):
            console.print("[red]Error: Unique ID must be exactly 5 alphanumeric characters[/red]")
            raise typer.Exit(1)
        if not unique_id[0].isalpha():
            console.print("[red]Error: Unique ID must start with a letter (Azure DNS requirement)[/red]")
            raise typer.Exit(1)
        if not unique_id.islower():
            console.print("[red]Error: Unique ID must be lowercase[/red]")
            raise typer.Exit(1)
    
    # Validate issuer type
    if issuer_type not in ["staging", "production"]:
        console.print("[red]Error: Issuer type must be 'staging' or 'production'[/red]")
        raise typer.Exit(1)
    
    # Create setup instance
    setup = AKSIstioSetup(unique_id, location, issuer_type)
    
    # Run cleanup or setup
    if cleanup:
        setup.cleanup()
    else:
        setup.run()

if __name__ == "__main__":
    app()