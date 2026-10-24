'use strict';

(() => {
  const messages = {
    INVALID_ARCHIVE: '记录格式不正确，未保存任何数据。请选择本工具导出的 JSON 或 CSV。',
    INVALID_CSV: 'CSV 格式无法识别，请选择抽卡记录导出文件。',
    ACCOUNT_ID_MISMATCH: '文件与所选存档属于不同账号。请选择自动识别，避免混合账号。',
    ACCOUNT_NOT_FOUND: '这个存档已不存在，请刷新账号列表。',
    RECORD_CONFLICT: '同一条记录出现不同的抽卡内容。为避免重复计数，本次未合并；可以新建独立存档核对。',
    ARCHIVE_TOO_LARGE: '记录超出限制：文件最多 30 MB，每个账号最多 10 万条记录、100 万抽。',
    LOCAL_STORAGE_ERROR: '无法写入本机存档，请检查程序目录是否可写、磁盘空间是否充足。',
    INVALID_LABEL: '请输入 1–60 个字符的存档名称。',
  };
  Object.assign(errors, messages);
  let accounts = [];
  let working = false;
  const selected = () => state.report?.storage === 'local' ? state.report.account?.id : '';

  async function api(action, body = {}) {
    const response = await fetch(`./api/library${action ? '/' + action : ''}`, {
      method: action ? 'POST' : 'GET', cache: 'no-store',
      ...(action ? {headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)} : {}),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(messages[result.error] || '本地操作未完成，请检查程序是否正在运行。');
    return result;
  }

  function controls() {
    $('rename-account').disabled = $('delete-account').disabled = !selected() || working || state.busy;
    $('account-select').disabled = working || state.busy;
  }

  async function refresh(restore = false) {
    const info = await api(''); accounts = info.accounts;
    $('account-count').textContent = `${accounts.length} 个账号`;
    $('account-select').replaceChildren(new Option('选择一个账号存档', ''));
    $('import-target').replaceChildren(new Option('自动识别账号；无身份时新建存档', ''));
    for (const a of accounts) {
      $('account-select').append(new Option(`${a.label} · ${a.pull_count.toLocaleString()} 抽`, a.id));
      $('import-target').append(new Option(`合并到：${a.label}`, a.id));
    }
    $('account-select').value = selected() || '';
    $('storage-path').textContent = info.directory;
    controls();
    document.dispatchEvent(new CustomEvent('library-changed', {detail: info}));
    if (restore && info.last_account && accounts.some(a => a.id === info.last_account)) {
      showReport(await api('open', {id: info.last_account}));
    }
    return info;
  }

  async function run(operation) {
    if (working || state.busy) {status('正在处理记录，请稍候。'); return;}
    working = true; controls(); $('submit').disabled = true;
    try {await operation();}
    catch (error) {status(error.message || '操作未完成。', 'error');}
    finally {working = false; controls(); $('submit').disabled = state.busy;}
  }

  function save(blob, filename) {
    const url = URL.createObjectURL(blob); const a = document.createElement('a');
    a.href = url; a.download = filename; document.body.append(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 60000);
  }

  $('account-select').addEventListener('change', () => run(async () => {
    const id = $('account-select').value;
    if (!id) {clearResults(); controls(); return;}
    const report = await api('open', {id}); showReport(report); status('已打开本机存档。');
  }));
  $('rename-account').addEventListener('click', () => run(async () => {
    if (!selected()) return;
    const label = prompt('存档名称（最多 60 个字符）', state.report.account.label);
    if (label === null) return;
    showReport(await api('rename', {id: selected(), label})); status('存档名称已保存。');
  }));
  $('delete-account').addEventListener('click', () => run(async () => {
    if (!selected() || !confirm(`删除“${state.report.account.label}”的本机存档？\n仅删除本地记录，不影响游戏账号。未备份的记录将无法恢复。`)) return;
    await api('delete', {id: selected()}); clearResults();
    const info = await refresh();
    if (info.accounts.length) showReport(await api('open', {id: info.accounts[0].id}));
    status('已删除所选本机存档。');
  }));
  $('backup-all').addEventListener('click', () => run(async () => {
    if (!accounts.length) {status('还没有本地存档，请先查询或导入记录。'); return;}
    const backup = await api('backup');
    save(new Blob([JSON.stringify(backup, null, 2)], {type: 'application/json'}), `星落-全部账号备份-${new Date().toISOString().slice(0, 10)}.json`);
    status(`已生成 ${backup.accounts.length} 个账号的备份文件。`);
  }));
  $('import-open').addEventListener('click', () => {
    if (working || state.busy) {status('正在处理记录，请稍候。'); return;}
    $('import-error').hidden = true; $('import-dialog').showModal();
  });
  $('import-close').addEventListener('click', () => $('import-dialog').close());
  $('import-form').addEventListener('submit', async event => {
    event.preventDefault();
    const file = $('import-file').files[0];
    if (!file || working || state.busy) return;
    working = true; controls(); $('import-submit').disabled = $('submit').disabled = true;
    $('import-error').hidden = true;
    try {
      if (file.size > 30 * 1024 * 1024) throw new Error(messages.ARCHIVE_TOO_LARGE);
      const fmt = file.name.toLowerCase().endsWith('.csv') ? 'csv' : 'json';
      const result = await api('import', {content: await file.text(), format: fmt,
        target: $('import-target').value || null, label: $('import-label').value.trim()});
      showReport(result.reports[result.reports.length - 1]); await refresh();
      $('import-dialog').close(); $('import-file').value = ''; $('import-label').value = '';
      status(`已导入并合并 ${result.reports.length} 个账号存档；重复记录已去除。`);
    } catch (error) {$('import-error').textContent = error.message; $('import-error').hidden = false;}
    finally {working = false; controls(); $('import-submit').disabled = $('submit').disabled = false;}
  });
  document.addEventListener('report-shown', () => {refresh().catch(error => status(error.message, 'error'));});
  $('clear-results').addEventListener('click', () => {$('account-select').value = ''; controls();});
  $('query-form').addEventListener('submit', () => {controls();});
  refresh(new URLSearchParams(location.search).get('preview') !== '1').catch(error => status(error.message, 'error'));
  window.LocalArchive = {api, refresh, save};
})();
