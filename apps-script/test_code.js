// Runs Code.gs with fake Google parts, so you can check it with no account. Run: node apps-script/test_code.js
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

function run(webhook, cell, status) {
  const sent = [];
  const triggers = [];
  const deleted = [];
  const ctx = {
    SpreadsheetApp: {getActive: () => ({getSheetByName: (n) => {
      assert.strictEqual(n, 'Monday in Slack');
      return {getRange: (a) => { assert.strictEqual(a, 'B4'); return {getDisplayValue: () => cell}; }};
    }})},
    PropertiesService: {getScriptProperties: () => ({getProperty: (k) => (k === 'SLACK_WEBHOOK_URL' ? webhook : null)})},
    UrlFetchApp: {fetch: (u, o) => { sent.push({u, o}); return {getResponseCode: () => status}; }},
    ScriptApp: {
      WeekDay: {MONDAY: 'MONDAY'},
      getProjectTriggers: () => [{getHandlerFunction: () => 'postMondayMessage'}, {getHandlerFunction: () => 'other'}],
      deleteTrigger: (t) => deleted.push(t.getHandlerFunction()),
      newTrigger: (f) => ({timeBased: () => ({onWeekDay: (d) => ({atHour: (h) => ({create: () => triggers.push({f, d, h})})})})}),
    },
  };
  vm.createContext(ctx);
  vm.runInContext(fs.readFileSync(path.join(__dirname, 'Code.gs'), 'utf8'), ctx);
  return {ctx, sent, triggers, deleted};
}

let r = run('https://example.test/hook', 'Client health, Monday', 200);
r.ctx.postMondayMessage();
assert.strictEqual(r.sent[0].u, 'https://example.test/hook');
assert.deepStrictEqual(JSON.parse(r.sent[0].o.payload), {text: 'Client health, Monday'});

r = run('REPLACE_ME', 'x', 200);
assert.throws(() => r.ctx.postMondayMessage(), /SLACK_WEBHOOK_URL/);
assert.strictEqual(r.sent.length, 0);

r = run('https://example.test/hook', '', 200);
assert.throws(() => r.ctx.postMondayMessage(), /empty/);

r = run('https://example.test/hook', 'x', 404);
assert.throws(() => r.ctx.postMondayMessage(), /404/);

r = run('https://example.test/hook', 'x', 200);
r.ctx.installMondayTrigger();
assert.deepStrictEqual(r.deleted, ['postMondayMessage']);
assert.deepStrictEqual(r.triggers, [{f: 'postMondayMessage', d: 'MONDAY', h: 8}]);

console.log('Code.gs: all checks pass');
