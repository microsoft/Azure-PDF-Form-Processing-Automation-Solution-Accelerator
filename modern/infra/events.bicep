param storageName string
param workflowName string
resource source 'Microsoft.Storage/storageAccounts@2023-05-01' existing = { name: storageName }
resource workflowHost 'Microsoft.Web/sites@2024-04-01' existing = { name: workflowName }
var trigger = '${workflowHost.id}/hostruntime/runtime/webhooks/workflow/api/management/workflows/process/triggers/receive'
resource completed 'Microsoft.EventGrid/eventSubscriptions@2022-06-15' = {
  name: 'raw-pdf-completed'
  scope: source
  properties: {
    eventDeliverySchema: 'EventGridSchema'
    destination: { endpointType: 'WebHook', properties: { endpointUrl: listCallbackUrl(trigger, '2018-11-01').value, maxEventsPerBatch: 1 } }
    filter: {
      includedEventTypes: ['Microsoft.Storage.BlobCreated']
      subjectBeginsWith: '/blobServices/default/containers/raw/blobs/'
      subjectEndsWith: '.pdf'
      isSubjectCaseSensitive: false
      advancedFilters: [{ operatorType: 'StringIn', key: 'data.api', values: ['PutBlob', 'PutBlockList', 'FlushWithClose'] }]
    }
  }
}