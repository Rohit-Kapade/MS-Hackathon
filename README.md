# Financial Data Extraction — Model Benchmarking

## Background

For details on the plan for the hack, refer to the [plan](./docs/plan.md).

## Getting Started

Before we can get started developing in the repository, we need to get access to
the repository and the Azure Subscription. Make sure that you:

- Request an invitation from [Bastian](mailto:bastian.burger@microsoft.com). You
  will receive two invitations in the following:
  - Accept the invitation for the Azure Tenant first (requires MFA setup using
    Microsoft Authenticator).
  - Accept the invitation for the ADO Project. Try opening the link
    <https://dev.azure.com/foundry-hack/foundry-hack>. If it shows a 404 and
    prompts you to add details, add your personal details and try again.
- Make sure that you can see the resource group `rg-hack-main` in the [Contoso
  tenant](https://portal.azure.com/MngEnvMCAP623781.onmicrosoft.com).

To clone the repository, make sure to follow the instructions on setting up
access to [Azure Repos].

[Azure Repos]:
    https://learn.microsoft.com/azure/devops/repos/git/set-up-credential-managers?view=azure-devops

### Development environment

The repository includes a [Dev Container] configuration so you get a
reproducible, fully configured environment. You can either run it on a
**cloud-hosted VM** (provided for the hackathon) or **locally** with Docker on
your own machine.

[Dev Container]: https://containers.dev/

#### Cloud VM + Dev Container

For the hackathon we provide cloud-hosted Ubuntu VMs with Docker and all
dependencies pre-installed. Users connect via **VS Code Remote-SSH** and then
*Reopen in Container*.

##### 1. Generate a dedicated SSH key pair

First, we create an SSH key pair that allows us to authenticate against the VM.
Open PowerShell and run:

```powershell
mkdir "$env:USERPROFILE\.ssh"
ssh-keygen -t ed25519 -f "$env:USERPROFILE\.ssh\id_foundryhack" -C "foundryhack"
cat "$env:USERPROFILE\.ssh\id_foundryhack.pub"
```

When prompted, you can set a passphrase or leave it empty.

This creates two files:

| File | Purpose |
| --- | --- |
| `C:\Users\<you>\.ssh\id_foundryhack` | **Private key** — never share this |
| `C:\Users\<you>\.ssh\id_foundryhack.pub` | **Public key** — send this to the VM admin |

> **Note:** Windows 10/11 ships with OpenSSH built-in. If `ssh-keygen` is not
> found, enable it via *Settings → Apps → Optional Features → OpenSSH Client*.

##### 2. Deploy the VM (admin only)

A MSFT dev can deploy the VM for you. From the repository root:

```bash
# One-time: deploy shared VNet + NSG
./infra/deploy.sh base

# Per user: deploy the VM
./infra/deploy.sh vm "alice" "ssh-ed25519 AAAAC3Nza...the-users-public-key..."
```

This creates `vm-hack-alice`, `pip-hack-alice`, etc. The shared VNet and NSG
are deployed once via `deploy.sh base` and reused across all users.

##### 3. Connect with VS Code Remote-SSH

Create or edit `C:\Users\<you>\.ssh\config` and add:

```txt
Host hack-vm
    HostName <PUBLIC_IP_FROM_DEPLOYMENT_OUTPUT>
    User devuser
    IdentityFile C:\Users\<you>\.ssh\id_foundryhack
    ForwardAgent yes
```

Replace `<you>` with your Windows username and
`<PUBLIC_IP_FROM_DEPLOYMENT_OUTPUT>` with the IP shown after deployment.

Then in VS Code:

1. Install the `ms-vscode-remote.vscode-remote-extensionpack` ("Remote
   Development (Microsoft)") extension pack.
2. Open the Command Palette (`Ctrl+Shift+P`) → **Remote-SSH: Connect to Host…**
   -> select `hack-vm`, then `Linux` (if prompted).

##### 4. Clone the repository

Once connected to the VM, open a terminal in VS Code and set up another SSH key
to authenticate against Azure DevOps, which is where we store the code:

```bash
# Create the SSH key pair
ssh-keygen -t rsa-sha2-256 -f ~/.ssh/id_ado -C "ado"
# Tell SSH to use the ADO key for dev.azure.com
echo -e "Host ssh.dev.azure.com\n    IdentityFile ~/.ssh/id_ado" >> ~/.ssh/config
chmod 600 ~/.ssh/config
# Load the key into the agent for the current session
# (future sessions are handled automatically via .bashrc)
eval "$(ssh-agent -s)"
ssh-add ~/.ssh/id_ado
# Print the public SSH key
cat ~/.ssh/id_ado.pub
```

Add the public key to your Azure DevOps account by opening `User settings` >
`Public SSH keys` and pasting the public part of the SSH key (printed with the
command above) into the key list.

Then clone and open the repo:

```bash
git clone git@ssh.dev.azure.com:v3/foundry-hack/foundry-hack/foundry-hack
cd foundry-hack
code .
```

Open the Command Palette (`Ctrl+Shift+P`) and select `"Dev Containers: Rebuild
and Reopen in Container"`.

Run:

```sh
./scripts/initialize
```

to log in with your account to Azure. This tests whether you can access the
deployed resources.

#### Local Dev Container

The repo ships with a Dev Container that includes Python 3, uv, Azure CLI, and
all VS Code extensions pre-configured. If you have Docker installed on your
local machine, then you can use that.

1. Follow [the instructions to get started developing inside a
   container][devcontainer]. It's best for performance to clone the repository
   inside WSL. Cloning it on the Windows file system will work, too, but may
   come with some performance tradeoffs.
2. Open the cloned repository in VS Code, then open the Command Palette
   (`Ctrl+Shift+P`) and select **Dev Containers: Reopen in Container**.
3. Run:

    ```sh
    ./scripts/initialize
    ```

    to log in with your account to Azure. This tests whether you can access the
    deployed resources.

[devcontainer]: https://code.visualstudio.com/docs/devcontainers/containers

## PDF Structured Data Extractor

Extracts structured information from PDFs using Azure AI Foundry / Azure OpenAI
and evaluates the results against a ground-truth JSON definition.

### Environment variables

Copy `.env.example` to `.env` and fill in your values:

| Variable | Description |
| --- | --- |
| `AZURE_FOUNDRY_PROJECT_ENDPOINT` | AI Foundry project endpoint URL |
| `AZURE_TENANT_ID` | Azure AD tenant ID |
| `AZURE_SUBSCRIPTION_ID` | Azure subscription ID |

You must also be logged in via `az login` (the extractor uses
`AzureCliCredential`).

### Install dependencies

```bash
uv sync
```

Or with pip:

```bash
pip install -e '.[dev]'
```

### Run the extractor

```bash
uv run python src/pdf_structured_extractor.py \
  --pdf ESG-files/data/BASF_Key_issue_assessment.pdf \
  --json ESG-files/ground_truth/BASF_Key_issue_assessment.json \
  --output-dir output
```

### Output files

| File | Description |
| --- | --- |
| `extracted_output.json` | Structured JSON extracted by the model |
| `evaluation_report.json` | Field-by-field comparison with ground truth |

### Sample input/output

**Input JSON** (`BASF_Key_issue_assessment.json`):

```json
{
  "query": "ESG Rating scorecard - ENVIRONMENT - Carbon Emissions - KEY ISSUE ASSESSMENT",
  "prompt": "Go to the 'KEY ISSUE ASSESSMENT' section and extract ...",
  "context": "KEY ISSUE ASSESSMENT",
  "ground_truth": {
    "RISK EXPOSURE ASSESSMENT": { "Company": "6.4", "Industry": "6.4" },
    "RISK MANAGEMENT ASSESSMENT": { "Company": "6.5", "Industry": "6.1" }
  }
}
```

**Sample extracted output** (`extracted_output.json`):

```json
{
  "RISK EXPOSURE ASSESSMENT": { "Company": "6.4", "Industry": "6.4" },
  "RISK MANAGEMENT ASSESSMENT": { "Company": "6.5", "Industry": "6.1" }
}
```

**Sample evaluation report** (`evaluation_report.json`):

```json
{
  "field_results": {
    "RISK EXPOSURE ASSESSMENT": {
      "Company": { "expected": "6.4", "extracted": "6.4", "match": true },
      "Industry": { "expected": "6.4", "extracted": "6.4", "match": true }
    },
    "RISK MANAGEMENT ASSESSMENT": {
      "Company": { "expected": "6.5", "extracted": "6.5", "match": true },
      "Industry": { "expected": "6.1", "extracted": "6.1", "match": true }
    }
  },
  "summary": {
    "total_fields": 4,
    "matches": 4,
    "mismatches": 0,
    "accuracy_percent": 100.0
  }
}
```
