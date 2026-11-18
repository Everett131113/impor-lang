// Impor (I++) VS Code 扩展客户端（纯 JS，无需 TypeScript 编译）
// 启动 `python -m impor lsp` 作为 LSP 服务器（stdio）；格式化走 LSP textDocument/formatting。
'use strict';

const vscode = require('vscode');
const { spawn } = require('child_process');
const {
  LanguageClient, TransportKind
} = require('vscode-languageclient/node');

let client = null;
let outputChannel = null;

function pythonExec() {
  const cfg = vscode.workspace.getConfiguration('impor');
  return cfg.get('pythonPath', 'python');
}

function activate(context) {
  outputChannel = vscode.window.createOutputChannel('Impor Language Server');
  context.subscriptions.push(outputChannel);

  // DAP：复用 `python -m impor dap`
  try {
    if (vscode.debug && vscode.debug.registerDebugAdapterDescriptorFactory) {
      context.subscriptions.push(vscode.debug.registerDebugAdapterDescriptorFactory('impor', {
        createDebugAdapterDescriptor() {
          return new vscode.DebugAdapterExecutable(pythonExec(), ['-m', 'impor', 'dap']);
        }
      }));
    }
  } catch (err) {
    outputChannel.appendLine(`[dap] ${err && err.message ? err.message : err}`);
  }

  const serverOptions = () => {
    return new Promise((resolve, reject) => {
      const py = pythonExec();
      const child = spawn(py, ['-m', 'impor', 'lsp'], {
        stdio: ['pipe', 'pipe', 'pipe']
      });
      child.on('error', (err) => {
        vscode.window.showErrorMessage(
          `Impor LSP 启动失败（${py} -m impor lsp）：${err.message}\n` +
          '请先安装 Impor：cd ImporLang && python -m pip install -e .'
        );
        reject(err);
      });
      // 必须消费 stderr：否则管道写满后 LSP 服务会阻塞
      child.stderr.on('data', (data) => {
        outputChannel.appendLine(`[stderr] ${data.toString().trimEnd()}`);
      });
      child.on('exit', (code, signal) => {
        if (code !== 0 && code !== null) {
          outputChannel.appendLine(`[exit] code=${code} signal=${signal || ''}`);
        }
      });
      resolve({ reader: child.stdout, writer: child.stdin });
    });
  };

  const clientOptions = {
    documentSelector: [{ language: 'impor', scheme: 'file' }],
    synchronize: { configurationSection: 'impor' },
    outputChannel
  };

  client = new LanguageClient('impor-lsp', 'Impor (I++) Language Server',
    serverOptions, clientOptions);
  client.start().catch((err) => {
    outputChannel.appendLine(`[start] ${err && err.message ? err.message : err}`);
  });

  // 运行当前脚本
  context.subscriptions.push(vscode.commands.registerCommand('impor.runFile', async () => {
    const ed = vscode.window.activeTextEditor;
    if (!ed) return;
    const f = ed.document.fileName;
    const term = vscode.window.createTerminal('Impor');
    term.sendText(`${pythonExec()} -m impor run "${f}"`);
    term.show();
  }));

  // 格式化当前脚本：优先走 LSP，同一套 impor fmt 规则
  context.subscriptions.push(vscode.commands.registerCommand('impor.formatFile', async () => {
    const ed = vscode.window.activeTextEditor;
    if (!ed) return;
    try {
      await vscode.commands.executeCommand('editor.action.formatDocument');
      vscode.window.showInformationMessage('Impor 已格式化');
    } catch (err) {
      vscode.window.showErrorMessage(`格式化失败: ${err && err.message ? err.message : err}`);
    }
  }));

  // CodeLens：点击“N 处引用”跳到引用列表
  context.subscriptions.push(vscode.commands.registerCommand('impor.showReferences', async (uri, line, character) => {
    try {
      const doc = await vscode.workspace.openTextDocument(vscode.Uri.parse(uri));
      const pos = new vscode.Position(line, character);
      const locations = await vscode.commands.executeCommand(
        'vscode.executeReferenceProvider', doc.uri, pos);
      await vscode.commands.executeCommand('editor.action.showReferences', doc.uri, pos, locations || []);
    } catch (err) {
      vscode.window.showErrorMessage(`查询引用失败: ${err && err.message ? err.message : err}`);
    }
  }));
}

function deactivate() {
  if (client) {
    return client.stop();
  }
}

module.exports = { activate, deactivate };
