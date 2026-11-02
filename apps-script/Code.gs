// Client health template. Sends the Monday in Slack tab to your Slack.
// Your Slack webhook goes in Project Settings, Script properties, as SLACK_WEBHOOK_URL.
// Never type it into the sheet. Anyone who can open the sheet could read it there.

function postMondayMessage() {
  var tab = SpreadsheetApp.getActive().getSheetByName('Monday in Slack');
  var text = tab.getRange('B4').getDisplayValue();
  if (!text) throw new Error('The Monday in Slack tab is empty.');
  var url = PropertiesService.getScriptProperties().getProperty('SLACK_WEBHOOK_URL');
  if (!url || url === 'REPLACE_ME') throw new Error('Add SLACK_WEBHOOK_URL in Project Settings, Script properties.');
  var res = UrlFetchApp.fetch(url, {
    method: 'post',
    contentType: 'application/json',
    payload: JSON.stringify({text: text}),
    muteHttpExceptions: true
  });
  if (res.getResponseCode() !== 200) throw new Error('Slack answered with status ' + res.getResponseCode());
}

// Run this one time. It sends the message every Monday at about 8 in the morning.
function installMondayTrigger() {
  ScriptApp.getProjectTriggers().forEach(function (t) {
    if (t.getHandlerFunction() === 'postMondayMessage') ScriptApp.deleteTrigger(t);
  });
  ScriptApp.newTrigger('postMondayMessage').timeBased().onWeekDay(ScriptApp.WeekDay.MONDAY).atHour(8).create();
}
