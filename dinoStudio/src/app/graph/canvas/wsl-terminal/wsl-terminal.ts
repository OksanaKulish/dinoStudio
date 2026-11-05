import { ChangeDetectionStrategy, Component, signal } from '@angular/core';
import { fetchLocalApi } from '../../../local-api';

interface TerminalResult {
	cwd: string;
	stdout?: string;
	stderr?: string;
	code?: number;
}

@Component({
	selector: 'app-wsl-terminal',
	standalone: true,
	changeDetection: ChangeDetectionStrategy.OnPush,
	templateUrl: './wsl-terminal.html',
	styleUrl: './wsl-terminal.scss',
})
export class WslTerminalComponent {
	protected readonly folder = signal('');
	protected readonly cwd = signal('');
	protected readonly command = signal('');
	protected readonly busy = signal(false);
	protected readonly history = signal<string[]>([]);

	protected async openFolder(): Promise<void> {
		if (this.busy() || !this.folder().trim()) return;
		this.busy.set(true);
		try {
			const result = await this.request('/open', { folder: this.folder().trim() });
			this.cwd.set(result.cwd);
			this.history.set([]);
		} catch (error) {
			this.append(String(error));
		} finally {
			this.busy.set(false);
		}
	}

	protected async run(): Promise<void> {
		const command = this.command().trim();
		if (this.busy() || !this.cwd() || !command) return;
		this.command.set('');
		this.append(`${this.cwd()} $ ${command}`);
		this.busy.set(true);
		try {
			const result = await this.request('/run', { cwd: this.cwd(), command });
			this.cwd.set(result.cwd);
			this.append([result.stdout, result.stderr, result.code ? `exit ${result.code}` : ''].filter(Boolean).join('\n'));
		} catch (error) {
			this.append(String(error));
		} finally {
			this.busy.set(false);
		}
	}

	private append(text: string): void {
		if (text) this.history.update(lines => [...lines, text].slice(-100));
	}

	private async request(action: string, body: object): Promise<TerminalResult> {
		const response = await fetchLocalApi(`/api/terminal${action}`, body);
		const result = await response.json();
		if (!response.ok) throw new Error(result.error ?? 'WSL command failed.');
		return result as TerminalResult;
	}
}