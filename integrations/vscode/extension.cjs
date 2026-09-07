const vscode = require('vscode');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const {spawn} = require('node:child_process');
const {capture, relative} = require('./context.cjs');

function execute(python, args, cwd) {
  return new Promise((resolve, reject) => {
    const process = spawn(python, args, {cwd, shell: false, windowsHide: true});
    let output = '', errors = '';
    process.stdout.setEncoding('utf8'); process.stderr.setEncoding('utf8');
    process.stdout.on('data', value => {
      output += value;
      if (output.length > 1048576) { process.kill(); reject(new Error('CLI output exceeded 1 MiB.')); }
    });
    process.stderr.on('data', value => { errors = (errors + value).slice(-4000); });
    process.on('error', reject);
    process.on('close', code => {
      if (code) reject(new Error(errors || `CLI exited with ${code}`));
      else { try { resolve(JSON.parse(output)); } catch (error) { reject(error); } }
    });
  });
}

function activate(context) {
  const run = action => async () => {
    if (!vscode.workspace.isTrusted) return;
    const editor = vscode.window.activeTextEditor;
    const folder = editor && vscode.workspace.getWorkspaceFolder(editor.document.uri) || vscode.workspace.workspaceFolders?.[0];
    if (!folder) { vscode.window.showErrorMessage('Open a source folder first.'); return; }
    const config = vscode.workspace.getConfiguration('codeSearchLab');
    const cli = config.get('cliPath');
    if (!cli || !path.isAbsolute(cli)) { vscode.window.showErrorMessage('Set codeSearchLab.cliPath to the codesearch entry point.'); return; }
    const root = folder.uri.fsPath;
    let temporary;
    try {
      if (action === 'start') {
        const args = [cli, 'start', '--repo', root, '--json'];
        if (config.get('warmModel')) args.push('--warm-model');
        await execute(config.get('python'), args, root);
        vscode.window.showInformationMessage('Code Search Lab service is ready.'); return;
      }
      const captured = capture(vscode, root, config.get('includeGitDiff'));
      const query = await vscode.window.showInputBox({prompt: 'Find source code', value: captured.selection?.text.slice(0, 1000) || ''});
      if (!query) return;
      temporary = await fs.mkdtemp(path.join(os.tmpdir(), 'codesearch-context-'));
      const file = path.join(temporary, 'context.json');
      await fs.writeFile(file, JSON.stringify(captured), {encoding: 'utf8', mode: 0o600});
      const args = [cli, 'search', query, '--repo', root, '--context', file, '--mode', config.get('mode'), '--json'];
      if (captured.active_file && !editor.document.isDirty) args.push('--sync-path', captured.active_file);
      const answer = await execute(config.get('python'), args, root);
      const choices = answer.results.map(result => ({label: result.qualified_symbol || result.symbol || result.text || result.file,
        description: `${result.file}:${result.start || result.line}`, result}));
      const chosen = await vscode.window.showQuickPick(choices, {placeHolder: `${answer.route.mode}: choose a source location`});
      if (chosen) {
        const destination = await fs.realpath(path.resolve(root, chosen.result.file));
        if (relative(await fs.realpath(root), destination) === null) throw new Error('Result is outside the source folder.');
        const document = await vscode.workspace.openTextDocument(destination);
        const view = await vscode.window.showTextDocument(document);
        const line = Math.max(0, (chosen.result.start || chosen.result.line) - 1);
        const range = new vscode.Range(line, 0, line, 0);
        view.selection = new vscode.Selection(range.start, range.end);
        view.revealRange(range);
      }
    } catch (error) { vscode.window.showErrorMessage(String(error.message || error)); }
    finally { if (temporary) await fs.rm(temporary, {recursive: true, force: true}); }
  };
  context.subscriptions.push(vscode.commands.registerCommand('codeSearchLab.search', run('search')));
  context.subscriptions.push(vscode.commands.registerCommand('codeSearchLab.start', run('start')));
}
module.exports = {activate};
