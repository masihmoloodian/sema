import * as vscode from 'vscode';
import { SemaClient } from './semaClient';

/** Status-bar item reflecting sema index freshness, polled from `sema status --json`. */
export class StatusBar {
  private readonly item: vscode.StatusBarItem;

  constructor(private readonly makeClient: () => SemaClient | undefined) {
    this.item = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 100);
    this.item.command = 'sema.search';
    this.item.text = '$(database) sema';
    this.item.tooltip = 'sema — semantic code search';
    this.item.show();
  }

  get disposable(): vscode.Disposable {
    return this.item;
  }

  async refresh(): Promise<void> {
    const client = this.makeClient();
    if (!client) {
      this.item.text = '$(database) sema';
      return;
    }
    try {
      const { index } = await client.status();
      if (!index.exists) {
        this.item.text = '$(database) sema: no index';
        this.item.tooltip = 'No sema index — run `sema index .`';
        return;
      }
      const stale = index.stale === true;
      this.item.text = `${stale ? '$(warning)' : '$(database)'} sema: ${index.chunks ?? '?'}`;
      this.item.tooltip = new vscode.MarkdownString(
        `**sema index**\n\n` +
          `- ${index.chunks ?? '?'} chunks · ${index.files ?? '?'} files\n` +
          `- model: ${index.model ?? '?'}\n` +
          `- ${index.age_days ?? '?'} day(s) old${stale ? ' — consider re-indexing' : ''}`,
      );
    } catch (err) {
      this.item.text = '$(database) sema';
      this.item.tooltip = `sema: ${(err as Error).message}`;
    }
  }
}
