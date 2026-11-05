import { ChangeDetectionStrategy, Component, signal } from '@angular/core';
import { fetchLocalApi } from '../../../local-api';

type TableValue = string | number | boolean | null;

interface DataFrameResult {
	name: string;
	columns: string[];
	rows: TableValue[][];
	rowCount: number;
}

@Component({
	selector: 'app-data-table',
	standalone: true,
	changeDetection: ChangeDetectionStrategy.OnPush,
	templateUrl: './data-table.html',
	styleUrl: './data-table.scss',
})
export class DataTableComponent {
	protected readonly result = signal<DataFrameResult | null>(null);
	protected readonly busy = signal(false);
	protected readonly error = signal('');

	protected async upload(event: Event): Promise<void> {
		const input = event.target as HTMLInputElement;
		const file = input.files?.[0];
		if (!file) return;
		this.error.set('');
		if (file.size > 5_000_000) {
			this.error.set('Choose a file under 5 MB.');
			input.value = '';
			return;
		}

		this.busy.set(true);
		try {
			const buffer = new Uint8Array(await file.arrayBuffer());
			let binary = '';
			for (let offset = 0; offset < buffer.length; offset += 0x8000) {
				binary += String.fromCharCode(...buffer.subarray(offset, offset + 0x8000));
			}
			const response = await fetchLocalApi('/api/dataframe/read', {
				name: file.name,
				data: btoa(binary),
			});
			const result: DataFrameResult & { error?: string } = await response.json();
			if (!response.ok) throw new Error(result.error ?? 'Could not read this file.');
			this.result.set(result);
		} catch (error) {
			this.error.set(error instanceof Error ? error.message : 'Could not read this file.');
		} finally {
			this.busy.set(false);
			input.value = '';
		}
	}
}