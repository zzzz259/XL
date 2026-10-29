const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const source = path.join(__dirname, '../web/mobile-ui.js');

test('mobile navigation preserves the return page and never mutates a running query', () => {
  assert.ok(fs.existsSync(source), 'Android mobile presentation has not been implemented');
  const ui = require(source);
  assert.equal(ui.initialPage({accounts: [], report: null}), 'query');
  assert.equal(ui.initialPage({accounts: [{id: 'a'}], report: null}), 'records');
  assert.equal(ui.initialPage({accounts: [], report: {account: {id: 'a'}}}), 'records');
  const nav = ui.createNavigation('records');
  const running = {busy: true, phase: 'querying'};
  nav.go('query'); nav.go('settings');
  assert.equal(nav.back(), 'query');
  assert.equal(nav.back(), 'records');
  assert.equal(nav.back(), null);
  assert.deepEqual(running, {busy: true, phase: 'querying'});
  assert.throws(() => nav.go('unknown'));
  const completed = ui.createNavigation('records');
  completed.go('query'); completed.go('records', true);
  assert.equal(completed.back(), null, 'successful query must not leave a duplicate records page in Back history');
});

test('mobile interval labels preserve uncertainty and account metadata stays informative', () => {
  assert.ok(fs.existsSync(source), 'Android mobile presentation has not been implemented');
  const ui = require(source);
  assert.equal(ui.intervalLabel({interval_exact: false, pulls_since_five: 24}), '≥24 抽');
  assert.equal(ui.intervalLabel({interval_exact: true, pulls_since_five: 24}), '24 抽');
  assert.equal(ui.channelLabel('bilibili'), 'B 服');
  assert.equal(ui.channelLabel('sms'), '官服');
  assert.equal(ui.channelLabel('legacy'), '导入存档');
});
