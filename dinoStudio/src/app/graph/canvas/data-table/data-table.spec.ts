import { ComponentFixture, TestBed } from '@angular/core/testing';
import { DataTableComponent } from './data-table';

describe('DataTableComponent', () => {
	let fixture: ComponentFixture<DataTableComponent>;
	let fetchMock: ReturnType<typeof vi.fn>;

	beforeEach(async () => {
		fetchMock = vi.fn(async (input: RequestInfo | URL) => {
			const url = String(input);
			if (url.endsWith('/api/health')) {
				if (url.includes(':4301/')) return { ok: true, json: async () => ({ service: 'dino-studio' }) } as Response;
				throw new Error('Connection refused');
			}
			return {
				ok: true,
				json: async () => ({ name: 'people.csv', columns: ['name', 'score'], rows: [['Ada', 98]], rowCount: 1 }),
			} as Response;
		});
		vi.stubGlobal('fetch', fetchMock);
		await TestBed.configureTestingModule({ imports: [DataTableComponent] }).compileComponents();
		fixture = TestBed.createComponent(DataTableComponent);
		fixture.detectChanges();
	});

	afterEach(() => vi.unstubAllGlobals());

	it('uploads a supported file and renders the pandas dataframe', async () => {
		const file = new File(['name,score\nAda,98'], 'people.csv', { type: 'text/csv' });
		Object.defineProperty(file, 'arrayBuffer', { value: async () => new TextEncoder().encode('name,score\nAda,98').buffer });
		const input = fixture.nativeElement.querySelector<HTMLInputElement>('#data-file')!;
		Object.defineProperty(input, 'files', { configurable: true, value: [file] });
		input.dispatchEvent(new Event('change'));
		await fixture.whenStable();
		fixture.detectChanges();

		expect(fixture.nativeElement.querySelector('.table-meta').textContent).toContain('people.csv');
		expect(fixture.nativeElement.querySelector('tbody').textContent).toContain('Ada');
		const request = fetchMock.mock.calls.find(([url]) => String(url).endsWith('/api/dataframe/read'));
		expect(request).toBeTruthy();
		expect(JSON.parse(request![1]!.body as string).name).toBe('people.csv');
	});
});