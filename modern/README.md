# Process PDF safety forms with Content Understanding

Upload a Contoso safety form to the `raw` container. Event Grid notifies a stateful Logic Apps Standard workflow when the upload completes. A Python function on Flex Consumption reads the exact blob version with managed identity and submits the PDF bytes to Content Understanding. The workflow polls a short-lived status endpoint, then asks the function to validate every segment. The full result, with confidence and grounding, goes into `processed`; the operational record goes into Azure Cosmos DB for NoSQL.

This is a new deployment, not an in-place migration. It reuses the [original field definitions](../Deployment/Data/samples/train/contoso_set_1/fields.json), [test PDFs](../Deployment/Data/samples/test/contoso_set_1/), and the upstream pattern of using managed identity to write processed JSON. It replaces per-page splitting and synchronous recognition. The original Power BI report expects a different data shape. Do not run the legacy deployment scripts for this starter.

## Verify locally, without Azure

From `modern/`, use Python 3.12 and PowerShell 7. These steps do not sign in, deploy resources, or send forms to Azure.

```powershell
python -m venv .venv
. ./.venv/Scripts/Activate.ps1  # Windows; on Linux/macOS activate .venv/bin/activate
python -m pip install -r functions/requirements.txt
$env:PYTHONPATH = (Resolve-Path ./functions).Path
python -m unittest discover -s tests -v
python setup_analyzer.py
./deploy.ps1 -PackageOnly
```

The tests send an upstream sample PDF to a local HTTP server through the real analysis SDK. They also exercise the accepted, needs-review, and duplicate-delivery paths using service doubles. The last two commands print the proposed analyzer schemas and the directory containing the two deployment ZIPs. To check the infrastructure locally, compile both files in `infra/` with the Bicep CLI. Local checks do not establish cloud availability, model quality, or end-to-end connectivity.

## Prepare a sandbox

1. Select an existing resource group in a subscription where you can deploy resources and assign roles. Use a disposable environment for the starter resources. Check regional availability for Flex Consumption, Logic Apps Standard, ZRS storage, Content Understanding, and your model. The script defaults to East US; it doesn't reserve capacity. Analysis requests use `processingLocation=geography`, not a promise of single-region inference.
2. In that resource group, prepare a [Content Understanding resource](https://learn.microsoft.com/azure/ai-services/content-understanding/overview) with a custom subdomain and the required default model deployments configured. The sample analyzer uses the `gpt-4.1` completion model. Check that the selected model and geography are supported together. The deployer needs permission to create analyzers. The starter does not create, replace, or delete your Foundry account or model deployments.
3. Prepare a single-tenant Microsoft Entra application registration for the Function API. Expose the Application ID URI `api://<client-id>` and configure version 2 access tokens. No client secret is needed. The template enforces App Service authentication and restricts access to the workflow identity. The Python HTTP triggers rely on that boundary: **do not publish this package to an unprotected Function app**.
4. Install Azure CLI with Bicep support, sign in, and select the intended subscription. From `modern/`, create the extractor and its parent classifier with the command below. Supply the actual resource endpoint. Creation does not overwrite existing analyzers. For changes, use a fresh versioned `--id` and pass the same ID as `-AnalyzerId` when deploying.

```powershell
python setup_analyzer.py --apply-endpoint https://<custom-subdomain>.cognitiveservices.azure.com --id contoso-safety
```

The classifier segments mixed PDFs and routes known forms to the safety extractor. It also returns content for the `other` category so that an unrecognized page cannot silently disappear from the review decision. The starter requires `Findings` at confidence 0.9 on every segment. Change `fieldThresholds` in the infrastructure parameters for your schema, calibrate it on labeled examples, and version your policy with your analyzers. See [classification and segmentation](https://learn.microsoft.com/azure/ai-services/content-understanding/concepts/classifier).

## Deploy and check one document

The following command **creates billable resources**. It deploys the infrastructure, publishes the Function and workflow ZIPs, and only then creates the filtered Event Grid subscription. Replace the placeholders first. Resource names are derived from the resource group. WS1, provisioned Cosmos throughput, monitoring and model usage can incur charges even at low document volumes.

```powershell
./deploy.ps1 -Deploy -Subscription '<subscription-id>' -ResourceGroup '<group>' `
  -FoundryAccount '<existing-foundry-account>' -ApiClientId '<application-client-id>'
```

1. Confirm the `raw-pdf-completed` subscription succeeded. Grant your uploader **Storage Blob Data Contributor** on `raw`. Using a signed-in storage client, upload [a sample PDF](../Deployment/Data/samples/test/contoso_set_1/ContosoSafety360-Sample-1.pdf). Use unique blob names and files no larger than 16 MiB. Do not make the container public or give a storage URL to the analyzer.
2. In the `process` workflow run, check that **Submit** returns 202 and an ID, **Status** reaches `succeeded`, and **Validate** returns `accepted` or `needs_review`. Read `processed/<id>.json` and `forms/records` with authorized identities. Each record contains status, issue count, and a blob reference. When the extracted projection exceeds 512 KiB, use the JSON blob for field values. High-confidence documents are accepted; actual confidence depends on the PDF and the model.
3. To verify the exception path deterministically in a sandbox, temporarily add a nonexistent required field to `FIELD_THRESHOLDS`, restart the Function app, and upload the sample under a new name. Expect `needs_review` with a `missing_value` issue. Restore the original policy afterward. For a live acceptance check, inspect the extracted values against the form before tuning thresholds.
4. To check replay, resubmit the same workflow run without changing the source blob. Expect the same document ID and no second final record. Concurrent submissions or a crash between analyzer submission and recording the operation ID can still incur an extra analysis charge. Polling is bounded to 60 iterations and 15 minutes. A workflow failure after delivery acknowledgment is visible in run history: inspect the run and operation, then resubmit the run for a transient failure. For a permanently failed or expired analysis, correct the cause and upload a new blob version.

## Review exceptions

Grant a designated reviewer read access to `processed` and appropriately scoped Cosmos data-plane access. List pending documents in `forms/records` with `SELECT c.id, c.issueCount FROM c WHERE c.status = 'needs_review'`. From a trusted workstation, inspect the record and result. The commands below use the reviewer's Azure CLI sign-in. Treat their output as sensitive.

```powershell
python review.py '<cosmos-endpoint>' '<document-id>'
python review.py '<cosmos-endpoint>' '<document-id>' --etag '<exact _etag returned above>' --decision reject --reviewer '<reviewer-id>' --note 'Source page needs clarification'
```

Use `approve` or `reject`. A stale ETag or an already-decided record is rejected. The decision does not overwrite the source result. The reviewer label is operator-supplied; use identity-based access and diagnostics for independent auditing. A dedicated, access-controlled review service is preferable to direct database access in production.

For automated assistance, you can supply `-RemediationEndpoint` and `-RemediationAudience` for a **tenant-owned, Entra-protected workflow**. The starter posts only `documentId` after `needs_review` is persisted. Authorize the calling workflow identity, deduplicate notifications by document ID, and give the receiving workflow only the tools and data it needs. Keep outbound messages and consequential decisions subject to human approval. The starter does **not** deploy an autonomous remediation agent or a Teams approval UI. See [agentic workflows in Logic Apps](https://learn.microsoft.com/azure/logic-apps/agent-workflows-concepts).

## Connect a shared mailbox (optional)

Direct uploads work without Exchange. To enable the `intake` workflow, first deploy with mailbox intake disabled. Ask your Exchange administrator to grant the Standard workflow's managed identity **Application Mail.ReadWrite** scoped to the designated shared mailbox through Exchange Online application RBAC. Verify it can access that mailbox and cannot access another. Do not grant a parallel, tenant-wide Microsoft Graph Mail.ReadWrite application permission. Then rerun the deployment with `-Mailbox 'forms@example.com' -TrustedSenders 'sender@example.com;second@example.com'`.

Use a dedicated inbox. Every five minutes, the workflow inspects up to ten unread messages with attachments, checks the exact sender address, saves non-inline PDFs through managed identity, and marks handled messages read. Untrusted senders are skipped and their messages marked read. Idempotent blob creation protects against retries. If a message has more than 100 attachments, inspect the failed run and split or move the message before retrying. Use Exchange anti-spoofing and attachment scanning; an address allowlist alone does not authenticate a sender.

## Connect analytics (optional)

The template creates a single-write-region NoSQL account with seven-day continuous backup. It does not purchase a Fabric capacity, create a workspace, or publish a report. In a Fabric workspace on an appropriate capacity, create a [mirrored Azure Cosmos DB database](https://learn.microsoft.com/fabric/mirroring/azure-cosmos-db), select the `forms` database and `records` container, and start replication. Confirm the smoke-test document appears in the mirrored table before creating a Direct Lake semantic model. Group by `status` and count `id` for a first report; filter to `accepted` and `review_approved` for operational reporting. Keep raw form fields out of dashboards that don't need them.

For a network-restricted source, follow [private-network mirroring setup](https://learn.microsoft.com/fabric/mirroring/azure-cosmos-db-private-network). The same-region workspace, OAuth gateway connection, trusted-workspace bypass, and Fabric REST creation are separate setup steps. Ongoing replication does not traverse the gateway or the source private endpoint.

## Boundaries and cleanup

The starter disables anonymous blob access and shared keys on document storage. Function ingress uses Entra authentication; the Event Grid webhook uses a signed callback URL. The Standard workflow host has a separate account for its runtime files and storage key. These are authenticated **public-endpoint** resources, not a private-network production deployment. Configure network isolation, retention/deletion, alerts for failed runs and review backlogs, and recovery procedures before processing real forms. Optional search, agent knowledge bases, model evaluation and HA/DR are not deployed.

After a sandbox test, remove the starter resources, event subscription, role grants, optional Exchange grant, and temporary packaging artifacts. Remove the sample analyzers and any Fabric items separately if no longer used. **Do not delete a shared resource group or the existing Foundry resource.**