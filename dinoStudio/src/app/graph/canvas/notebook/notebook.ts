import { ChangeDetectionStrategy, Component, OnDestroy, signal } from '@angular/core';
import { fetchLocalApi } from '../../../local-api';

interface NotebookCell {
	cell_type: string;
	source: string | string[];
	metadata: Record<string, unknown>;
	outputs?: unknown[];
	execution_count?: number | null;
}

interface NotebookDocument {
	nbformat: number;
	nbformat_minor: number;
	metadata: Record<string, unknown>;
	cells: NotebookCell[];
}

interface NotebookOutput {
	text: string;
	html?: string;
}

interface NotebookTable {
	headers: string[];
	rows: string[][];
}

const example: NotebookDocument = {
	nbformat: 4,
	nbformat_minor: 5,
	metadata: { kernelspec: { name: 'python3', display_name: 'Python 3' } },
	cells: [{
		cell_type: 'code', metadata: { id: 'pandas-example', language: 'python' }, execution_count: null, outputs: [],
		source: `import pandas as pd
import numpy as np
from IPython.display import HTML, display

rng = np.random.default_rng()
df = pd.DataFrame(rng.integers(1, 100, size=(5, 3)), columns=['Alpha', 'Beta', 'Gamma'])
display(HTML(df.to_html(index=False)))`,
	}],
};

@Component({
	selector: 'app-notebook',
	standalone: true,
	changeDetection: ChangeDetectionStrategy.OnPush,
	templateUrl: './notebook.html',
	styleUrl: './notebook.scss',
})
export class NotebookComponent implements OnDestroy {
	protected readonly notebook = signal<NotebookDocument>(example);
	protected readonly name = signal('Pandas example');
	protected readonly results = signal<NotebookOutput[][]>([]);
	protected readonly executionCounts = signal<(number | null)[]>([]);
	protected readonly error = signal('');
	protected readonly running = signal(false);
	protected readonly runningCell = signal<number | null>(null);
	private sessionId: string | null = null;

	public ngOnDestroy(): void {
		void this.stopSession();
	}

	protected source(cell: NotebookCell): string {
		return Array.isArray(cell.source) ? cell.source.join('') : cell.source;
	}

	protected edit(index: number, source: string): void {
		this.notebook.update(notebook => ({
			...notebook,
			cells: notebook.cells.map((cell, cellIndex) => cellIndex === index ? { ...cell, source } : cell),
		}));
		this.results.update(results => results.map((outputs, cellIndex) => cellIndex === index ? [] : outputs));
		this.executionCounts.update(counts => counts.map((count, cellIndex) => cellIndex === index ? null : count));
	}

	protected addCell(kind: 'code' | 'markdown'): void {
		const cell: NotebookCell = kind === 'code'
			? { cell_type: 'code', metadata: { language: 'python' }, source: '', outputs: [], execution_count: null }
			: { cell_type: 'markdown', metadata: { language: 'markdown' }, source: '' };
		this.notebook.update(notebook => ({ ...notebook, cells: [...notebook.cells, cell] }));
	}

	protected async open(event: Event): Promise<void> {
		const input = event.target as HTMLInputElement;
		const file = input.files?.[0];
		if (!file) return;
		this.error.set('');
		try {
			if (file.size > 900_000) throw new Error('Notebook exceeds the upload limit.');
			const notebook: NotebookDocument = JSON.parse(await file.text());
			if (notebook.nbformat !== 4 || !Array.isArray(notebook.cells)) throw new Error('Choose a version 4 Jupyter notebook.');
			await this.stopSession();
			this.notebook.set(notebook);
			this.name.set(file.name);
			this.results.set([]);
			this.executionCounts.set([]);
		} catch (error) {
			this.error.set(String(error));
		} finally {
			input.value = '';
		}
	}

	protected async run(): Promise<void> {
		if (this.running()) return;
		this.running.set(true);
		this.error.set('');
		this.results.set([]);
		this.executionCounts.set([]);
		try {
			await this.stopSession();
			for (const [index, cell] of this.notebook().cells.entries()) {
				if (cell.cell_type !== 'code') continue;
				this.runningCell.set(index);
				await this.execute(index);
			}
		} catch (error) {
			this.error.set(String(error));
		} finally {
			this.runningCell.set(null);
			this.running.set(false);
		}
	}

	protected async runCell(index: number): Promise<void> {
		if (this.running() || this.notebook().cells[index]?.cell_type !== 'code') return;
		this.running.set(true);
		this.runningCell.set(index);
		this.error.set('');
		try {
			await this.execute(index);
		} catch (error) {
			this.error.set(String(error));
		} finally {
			this.runningCell.set(null);
			this.running.set(false);
		}
	}

	private async execute(index: number): Promise<void> {
		if (!this.sessionId) {
			const session = await this.request<{ sessionId: string }>('/start', {});
			this.sessionId = session.sessionId;
		}
		const result = await this.request<{ outputs: NotebookOutput[]; executionCount: number }>('/cell', {
			sessionId: this.sessionId, source: this.source(this.notebook().cells[index]),
		});
		this.results.update(results => Array.from({ length: this.notebook().cells.length }, (_, cellIndex) =>
			cellIndex === index ? result.outputs : results[cellIndex] ?? []));
		this.executionCounts.update(counts => Array.from({ length: this.notebook().cells.length }, (_, cellIndex) =>
			cellIndex === index ? result.executionCount : counts[cellIndex] ?? null));
	}

	private async stopSession(): Promise<void> {
		const sessionId = this.sessionId;
		this.sessionId = null;
		if (sessionId) {
			try { await this.request('/stop', { sessionId }, true); } catch { /* Kernel may have expired. */ }
		}
	}

	private async request<T>(action: string, body: object, keepalive = false): Promise<T> {
		const response = await fetchLocalApi(`/api/notebook${action}`, body, keepalive);
		const result = await response.json();
		if (!response.ok) throw new Error(result.error ?? 'Notebook execution failed.');
		return result as T;
	}

	protected asTable(output: NotebookOutput): NotebookTable | null {
		if (!output.html) return null;
		const document = new DOMParser().parseFromString(output.html, 'text/html');
		const table = document.querySelector('table');
		if (!table) return null;
		const headers = Array.from(table.querySelectorAll('thead th'), heading => heading.textContent?.trim() ?? '');
		const rows = Array.from(table.querySelectorAll('tbody tr'), row =>
			Array.from(row.querySelectorAll('th, td'), cell => cell.textContent?.trim() ?? ''));
		return { headers, rows };
	}
}