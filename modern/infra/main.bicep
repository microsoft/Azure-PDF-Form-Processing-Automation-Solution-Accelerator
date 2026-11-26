param location string = resourceGroup().location
@description('Existing Foundry resource in this resource group with configured Content Understanding model defaults.')
param foundryAccount string
@description('Client ID of a single-tenant app registration with Application ID URI api://<client-id>.')
param apiClientId string
param analyzerId string = 'contoso-safety'
param fieldThresholds string = '{"Findings":0.9}'
param mailbox string = ''
@description('Exact sender email addresses separated by semicolons, without whitespace.')
param trustedSenders string = ''
@description('Optional HTTPS endpoint for an Entra-protected remediation workflow. Do not supply a callback URL containing a secret.')
param remediationEndpoint string = ''
param remediationAudience string = ''
var enableMailbox = !empty(mailbox) && !empty(trustedSenders)

var suffix = uniqueString(resourceGroup().id)
var blobContributor = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'ba92f5b4-2d11-453d-a403-e96b0029c9fe')
var blobReader = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '2a2b9908-6ea1-4ae2-8e65-a410df84e7d1')
var blobOwner = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'b7e6dc6d-f1e8-4753-8033-0f276bb0955b')
var cuUser = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'a97b65f3-24c7-4388-baec-2e87135dc908')

resource foundry 'Microsoft.CognitiveServices/accounts@2024-10-01' existing = { name: foundryAccount }
resource data 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: 'pdfdata${suffix}'
  location: location
  kind: 'StorageV2'
  sku: { name: 'Standard_ZRS' }
  properties: { isHnsEnabled: true, allowBlobPublicAccess: false, allowSharedKeyAccess: false, minimumTlsVersion: 'TLS1_2', supportsHttpsTrafficOnly: true }
}
resource blobs 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = { parent: data, name: 'default' }
resource raw 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = { parent: blobs, name: 'raw', properties: { publicAccess: 'None' } }
resource processed 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = { parent: blobs, name: 'processed', properties: { publicAccess: 'None' } }
resource host 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: 'pdffn${suffix}'
  location: location
  kind: 'StorageV2'
  sku: { name: 'Standard_LRS' }
  properties: { allowBlobPublicAccess: false, allowSharedKeyAccess: false, minimumTlsVersion: 'TLS1_2', supportsHttpsTrafficOnly: true }
}
resource hostBlobs 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = { parent: host, name: 'default' }
resource packages 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = { parent: hostBlobs, name: 'packages', properties: { publicAccess: 'None' } }
resource workflowStorage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: 'pdfworkflow${suffix}'
  location: location
  kind: 'StorageV2'
  sku: { name: 'Standard_LRS' }
  properties: { allowBlobPublicAccess: false, minimumTlsVersion: 'TLS1_2', supportsHttpsTrafficOnly: true }
}
resource files 'Microsoft.Storage/storageAccounts/fileServices@2023-05-01' = { parent: workflowStorage, name: 'default' }
resource share 'Microsoft.Storage/storageAccounts/fileServices/shares@2023-05-01' = { parent: files, name: 'workflows' }
var runtimeConnection = 'DefaultEndpointsProtocol=https;AccountName=${workflowStorage.name};AccountKey=${workflowStorage.listKeys().keys[0].value};EndpointSuffix=${environment().suffixes.storage}'

resource cosmos 'Microsoft.DocumentDB/databaseAccounts@2024-05-15' = {
  name: 'pdfforms-${suffix}'
  location: location
  kind: 'GlobalDocumentDB'
  properties: {
    databaseAccountOfferType: 'Standard'
    disableLocalAuth: true
    locations: [{ locationName: location, failoverPriority: 0 }]
    backupPolicy: { type: 'Continuous', continuousModeProperties: { tier: 'Continuous7Days' } }
    consistencyPolicy: { defaultConsistencyLevel: 'Session' }
  }
}
resource db 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases@2024-05-15' = { parent: cosmos, name: 'forms', properties: { resource: { id: 'forms' } } }
resource records 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-05-15' = {
  parent: db
  name: 'records'
  properties: { resource: { id: 'records', partitionKey: { paths: ['/id'], kind: 'Hash' } }, options: { throughput: 400 } }
}
resource logs 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: 'pdf-logs-${suffix}'
  location: location
  properties: { retentionInDays: 30, sku: { name: 'PerGB2018' } }
}
resource insights 'Microsoft.Insights/components@2020-02-02' = {
  name: 'pdf-insights-${suffix}'
  location: location
  kind: 'web'
  properties: { Application_Type: 'web', WorkspaceResourceId: logs.id }
}
resource flex 'Microsoft.Web/serverfarms@2024-04-01' = {
  name: 'pdf-flex-${suffix}'
  location: location
  kind: 'functionapp'
  sku: { name: 'FC1', tier: 'FlexConsumption' }
  properties: { reserved: true }
}
resource workflowPlan 'Microsoft.Web/serverfarms@2024-04-01' = {
  name: 'pdf-ws-${suffix}'
  location: location
  sku: { name: 'WS1', tier: 'WorkflowStandard', capacity: 1 }
  properties: { reserved: false }
}
resource fn 'Microsoft.Web/sites@2024-04-01' = {
  name: 'pdf-functions-${suffix}'
  location: location
  identity: { type: 'SystemAssigned' }
  kind: 'functionapp,linux'
  properties: {
    httpsOnly: true
    serverFarmId: flex.id
    functionAppConfig: {
      runtime: { name: 'python', version: '3.12' }
      scaleAndConcurrency: { instanceMemoryMB: 2048, maximumInstanceCount: 40 }
      deployment: { storage: { type: 'blobContainer', value: '${host.properties.primaryEndpoints.blob}${packages.name}', authentication: { type: 'SystemAssignedIdentity' } } }
    }
    siteConfig: {
      minTlsVersion: '1.2'
      ftpsState: 'Disabled'
      appSettings: [
        { name: 'AzureWebJobsStorage__accountName', value: host.name }
        { name: 'AzureWebJobsStorage__credential', value: 'managedidentity' }
        { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: insights.properties.ConnectionString }
        { name: 'SOURCE_ACCOUNT_ID', value: data.id }
        { name: 'BLOB_ENDPOINT', value: data.properties.primaryEndpoints.blob }
        { name: 'COSMOS_ENDPOINT', value: cosmos.properties.documentEndpoint }
        { name: 'CU_ENDPOINT', value: foundry.properties.endpoint }
        { name: 'ANALYZER_ID', value: analyzerId }
        { name: 'FIELD_THRESHOLDS', value: fieldThresholds }
      ]
    }
  }
}
resource logic 'Microsoft.Web/sites@2024-04-01' = {
  name: 'pdf-workflows-${suffix}'
  location: location
  kind: 'functionapp,workflowapp'
  identity: { type: 'SystemAssigned' }
  properties: {
    serverFarmId: workflowPlan.id
    httpsOnly: true
    siteConfig: {
      alwaysOn: true
      minTlsVersion: '1.2'
      ftpsState: 'Disabled'
      appSettings: [
        { name: 'APP_KIND', value: 'workflowApp' }
        { name: 'AzureWebJobsStorage', value: runtimeConnection }
        { name: 'WEBSITE_CONTENTAZUREFILECONNECTIONSTRING', value: runtimeConnection }
        { name: 'WEBSITE_CONTENTSHARE', value: share.name }
        { name: 'FUNCTIONS_EXTENSION_VERSION', value: '~4' }
        { name: 'FUNCTIONS_WORKER_RUNTIME', value: 'dotnet' }
        { name: 'WEBSITE_NODE_DEFAULT_VERSION', value: '~22' }
        { name: 'FORMS_API', value: 'https://${fn.properties.defaultHostName}' }
        { name: 'FORMS_AUDIENCE', value: 'api://${apiClientId}' }
        { name: 'Workflows.intake.FlowState', value: enableMailbox ? 'Enabled' : 'Disabled' }
        { name: 'SHARED_MAILBOX', value: mailbox }
        { name: 'TRUSTED_SENDERS', value: trustedSenders }
        { name: 'REMEDIATION_ENDPOINT', value: remediationEndpoint }
        { name: 'REMEDIATION_AUDIENCE', value: remediationAudience }
        { name: 'RAW_BASE', value: '${data.properties.primaryEndpoints.blob}${raw.name}/' }
        { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: insights.properties.ConnectionString }
      ]
    }
  }
}
resource apiAuth 'Microsoft.Web/sites/config@2024-04-01' = {
  parent: fn
  name: 'authsettingsV2'
  properties: {
    platform: { enabled: true }
    globalValidation: { requireAuthentication: true, unauthenticatedClientAction: 'Return401' }
    identityProviders: { azureActiveDirectory: {
      enabled: true
      registration: { clientId: apiClientId, openIdIssuer: '${environment().authentication.loginEndpoint}${tenant().tenantId}/v2.0' }
      validation: { allowedAudiences: ['api://${apiClientId}', apiClientId], defaultAuthorizationPolicy: { allowedPrincipals: { identities: [logic.identity.principalId] } } }
    } }
    httpSettings: { requireHttps: true }
  }
}
resource readRaw 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: raw
  name: guid(raw.id, fn.id, blobReader)
  properties: { principalId: fn.identity.principalId, principalType: 'ServicePrincipal', roleDefinitionId: blobReader }
}
resource intakeAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (enableMailbox) {
  scope: raw
  name: guid(raw.id, logic.id, blobContributor)
  properties: { principalId: logic.identity.principalId, principalType: 'ServicePrincipal', roleDefinitionId: blobContributor }
}
resource writeProcessed 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: processed
  name: guid(processed.id, fn.id, blobContributor)
  properties: { principalId: fn.identity.principalId, principalType: 'ServicePrincipal', roleDefinitionId: blobContributor }
}
resource hostAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: host
  name: guid(host.id, fn.id, blobOwner)
  properties: { principalId: fn.identity.principalId, principalType: 'ServicePrincipal', roleDefinitionId: blobOwner }
}
resource analyzeAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: foundry
  name: guid(foundry.id, fn.id, cuUser)
  properties: { principalId: fn.identity.principalId, principalType: 'ServicePrincipal', roleDefinitionId: cuUser }
}
resource cosmosAccess 'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2024-05-15' = {
  parent: cosmos
  name: guid(records.id, fn.id, 'write')
  properties: {
    principalId: fn.identity.principalId
    roleDefinitionId: '${cosmos.id}/sqlRoleDefinitions/00000000-0000-0000-0000-000000000002'
    scope: '${cosmos.id}/dbs/${db.name}/colls/${records.name}'
  }
}
output functionName string = fn.name
output workflowName string = logic.name
output storageName string = data.name
output cosmosEndpoint string = cosmos.properties.documentEndpoint